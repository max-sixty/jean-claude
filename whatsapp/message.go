package main

import (
	"database/sql"
	"fmt"
	"os"
	"strings"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// NormalizedMessage contains the common fields extracted from both live events and history sync.
type NormalizedMessage struct {
	ID        string
	ChatJID   string
	SenderJID string
	PushName  string
	Timestamp int64
	IsFromMe  bool
	IsGroup   bool
	Message   *waE2E.Message
}

// MediaMetadata holds media information extracted from a WhatsApp message.
// Used for storing download info and later retrieval.
type MediaMetadata struct {
	MediaType     string // image, video, audio, document, sticker, contact, location
	MimeType      string // Full MIME type (e.g., image/jpeg)
	MediaKey      []byte // Decryption key
	FileSHA256    []byte // SHA256 hash of decrypted file
	FileEncSHA256 []byte // SHA256 hash of encrypted file
	FileLength    int64  // File size in bytes
	DirectPath    string // WhatsApp CDN path
	URL           string // Full download URL
}

// ReplyContext holds information about the message being replied to.
type ReplyContext struct {
	ID     string // ID of the quoted message
	Sender string // JID of quoted message sender
	Text   string // Preview of quoted text (first 200 chars)
}

// MessageContent holds full message content including media metadata.
type MessageContent struct {
	Text      string
	MediaType string
	Media     *MediaMetadata
	Reply     *ReplyContext
}

// normalizeFromEvent converts a live message event to NormalizedMessage.
func normalizeFromEvent(evt *events.Message) NormalizedMessage {
	return NormalizedMessage{
		ID:        evt.Info.ID,
		ChatJID:   evt.Info.Chat.String(),
		SenderJID: evt.Info.Sender.String(),
		PushName:  evt.Info.PushName,
		Timestamp: evt.Info.Timestamp.Unix(),
		IsFromMe:  evt.Info.IsFromMe,
		IsGroup:   evt.Info.Chat.Server == types.GroupServer,
		Message:   evt.Message,
	}
}

// normalizeFromHistory converts a history sync message to NormalizedMessage.
func normalizeFromHistory(chatJID string, msg *waWeb.WebMessageInfo) *NormalizedMessage {
	if msg == nil {
		return nil
	}
	key := msg.GetKey()
	if key == nil {
		return nil
	}

	isFromMe := key.GetFromMe()
	isGroup := strings.HasSuffix(chatJID, "@g.us")

	// Determine sender:
	// - Groups: use participant field
	// - DMs from others: use remoteJID (the other person)
	// - DMs from self: use our own JID
	var sender string
	switch {
	case isGroup:
		sender = msg.GetParticipant()
	case isFromMe:
		// DM from self - sender is our own JID
		if client.Store.ID != nil {
			sender = client.Store.ID.String()
		}
	default:
		// DM from other person
		sender = key.GetRemoteJID()
	}

	timestamp := int64(msg.GetMessageTimestamp())
	if timestamp == 0 {
		timestamp = time.Now().Unix()
	}

	return &NormalizedMessage{
		ID:        key.GetID(),
		ChatJID:   chatJID,
		SenderJID: sender,
		PushName:  msg.GetPushName(),
		Timestamp: timestamp,
		IsFromMe:  isFromMe,
		IsGroup:   isGroup,
		Message:   msg.GetMessage(),
	}
}

func saveMessage(evt *events.Message) error {
	normalized := normalizeFromEvent(evt)
	_, err := saveNormalizedMessage(&normalized, normalized.IsFromMe, true)
	return err
}

// saveHistoryMessageWithReadStatus saves a message from history sync with the specified read status.
// Returns (saved, err) where saved indicates if the message was inserted into the messages table
// (as opposed to skipped or saved as a reaction). This helps the caller track unread counts correctly.
func saveHistoryMessageWithReadStatus(chatJID string, msg *waWeb.WebMessageInfo, isRead bool) (bool, error) {
	normalized := normalizeFromHistory(chatJID, msg)
	if normalized == nil {
		return false, nil
	}
	return saveNormalizedMessage(normalized, isRead, false)
}

// saveNormalizedMessage saves a message to the database.
// isRead determines the initial read status.
// isLive indicates whether this is from a live event (updates text/media on conflict, triggers chat update).
// Returns (saved, err) where saved indicates if the message was inserted into the messages table.
// Reactions, protocol messages, and empty messages return saved=false.
func saveNormalizedMessage(msg *NormalizedMessage, isRead bool, isLive bool) (bool, error) {
	if msg.Message == nil {
		return false, nil
	}

	// Handle reaction messages separately - they go to reactions table, not messages
	if rm := msg.Message.GetReactionMessage(); rm != nil {
		return false, saveReaction(msg, rm)
	}

	content := extractMessageContentFull(msg.Message)

	// Skip system/protocol messages that have no user-visible content
	switch content.MediaType {
	case "key_distribution", "context_info", "protocol":
		return false, nil
	}

	// Skip if no content was extracted (unhandled message types)
	if content.MediaType == "" && content.Text == "" {
		return false, nil
	}

	// Save contact info from history sync messages (live events use PushName handler)
	if !isLive && msg.PushName != "" && msg.SenderJID != "" {
		_ = saveContact(msg.SenderJID, "", msg.PushName)
	}

	// Prepare media metadata for storage
	var mimeType, directPath, mediaURL sql.NullString
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength sql.NullInt64

	if content.Media != nil {
		mimeType = sql.NullString{String: content.Media.MimeType, Valid: content.Media.MimeType != ""}
		directPath = sql.NullString{String: content.Media.DirectPath, Valid: content.Media.DirectPath != ""}
		mediaURL = sql.NullString{String: content.Media.URL, Valid: content.Media.URL != ""}
		mediaKey = content.Media.MediaKey
		fileSHA256 = content.Media.FileSHA256
		fileEncSHA256 = content.Media.FileEncSHA256
		if content.Media.FileLength > 0 {
			fileLength = sql.NullInt64{Int64: content.Media.FileLength, Valid: true}
		}
	}

	// Prepare reply context for storage
	var replyToID, replyToSender, replyToText sql.NullString
	if content.Reply != nil {
		replyToID = sql.NullString{String: content.Reply.ID, Valid: content.Reply.ID != ""}
		replyToSender = sql.NullString{String: content.Reply.Sender, Valid: content.Reply.Sender != ""}
		replyToText = sql.NullString{String: content.Reply.Text, Valid: content.Reply.Text != ""}
	}

	// Choose SQL based on whether to update content on conflict (live messages can be edits)
	var err error
	if isLive {
		_, err = messageDB.Exec(`
			INSERT INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at,
				mime_type_full, media_key, file_sha256, file_enc_sha256, file_length, direct_path, media_url,
				reply_to_id, reply_to_sender, reply_to_text)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(id) DO UPDATE SET
				text = excluded.text,
				media_type = excluded.media_type,
				is_read = MAX(messages.is_read, excluded.is_read),
				mime_type_full = COALESCE(excluded.mime_type_full, messages.mime_type_full),
				media_key = COALESCE(excluded.media_key, messages.media_key),
				file_sha256 = COALESCE(excluded.file_sha256, messages.file_sha256),
				file_enc_sha256 = COALESCE(excluded.file_enc_sha256, messages.file_enc_sha256),
				file_length = COALESCE(excluded.file_length, messages.file_length),
				direct_path = COALESCE(excluded.direct_path, messages.direct_path),
				media_url = COALESCE(excluded.media_url, messages.media_url),
				reply_to_id = COALESCE(excluded.reply_to_id, messages.reply_to_id),
				reply_to_sender = COALESCE(excluded.reply_to_sender, messages.reply_to_sender),
				reply_to_text = COALESCE(excluded.reply_to_text, messages.reply_to_text)
		`, msg.ID, msg.ChatJID, msg.SenderJID, msg.PushName, msg.Timestamp,
			content.Text, content.MediaType, boolToInt(msg.IsFromMe), boolToInt(isRead), time.Now().Unix(),
			mimeType, mediaKey, fileSHA256, fileEncSHA256, fileLength, directPath, mediaURL,
			replyToID, replyToSender, replyToText)
	} else {
		// History sync: don't update text/media_type on conflict (preserve existing content)
		_, err = messageDB.Exec(`
			INSERT INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at,
				mime_type_full, media_key, file_sha256, file_enc_sha256, file_length, direct_path, media_url,
				reply_to_id, reply_to_sender, reply_to_text)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(id) DO UPDATE SET
				is_read = MAX(messages.is_read, excluded.is_read),
				mime_type_full = COALESCE(excluded.mime_type_full, messages.mime_type_full),
				media_key = COALESCE(excluded.media_key, messages.media_key),
				file_sha256 = COALESCE(excluded.file_sha256, messages.file_sha256),
				file_enc_sha256 = COALESCE(excluded.file_enc_sha256, messages.file_enc_sha256),
				file_length = COALESCE(excluded.file_length, messages.file_length),
				direct_path = COALESCE(excluded.direct_path, messages.direct_path),
				media_url = COALESCE(excluded.media_url, messages.media_url),
				reply_to_id = COALESCE(excluded.reply_to_id, messages.reply_to_id),
				reply_to_sender = COALESCE(excluded.reply_to_sender, messages.reply_to_sender),
				reply_to_text = COALESCE(excluded.reply_to_text, messages.reply_to_text)
		`, msg.ID, msg.ChatJID, msg.SenderJID, msg.PushName, msg.Timestamp,
			content.Text, content.MediaType, boolToInt(msg.IsFromMe), boolToInt(isRead), time.Now().Unix(),
			mimeType, mediaKey, fileSHA256, fileEncSHA256, fileLength, directPath, mediaURL,
			replyToID, replyToSender, replyToText)
	}

	if err == nil && isLive {
		// Update chat timestamp (best-effort, don't fail message save)
		_ = saveChat(msg.ChatJID, "", msg.IsGroup, msg.Timestamp, false)
	}

	return err == nil, err
}

func saveContact(jid, name, pushName string) error {
	_, err := messageDB.Exec(`
		INSERT OR REPLACE INTO contacts (jid, name, push_name, updated_at)
		VALUES (?, ?, ?, ?)
	`, jid, name, pushName, time.Now().Unix())
	return err
}

func saveChat(jid, name string, isGroup bool, lastMessageTime int64, markedAsUnread bool) error {
	// UPSERT: preserve name if we have it, update marked_as_unread only if setting to true
	// (unread_count is computed from messages table, not stored here)
	_, err := messageDB.Exec(`
		INSERT INTO chats (jid, name, is_group, last_message_time, marked_as_unread, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET
			name = CASE WHEN excluded.name != '' THEN excluded.name ELSE chats.name END,
			last_message_time = COALESCE(MAX(chats.last_message_time, excluded.last_message_time), excluded.last_message_time),
			marked_as_unread = MAX(chats.marked_as_unread, excluded.marked_as_unread),
			updated_at = excluded.updated_at
	`, jid, name, boolToInt(isGroup), lastMessageTime, boolToInt(markedAsUnread), time.Now().Unix())
	return err
}

func markMessageRead(msgID string) error {
	_, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE id = ?`, msgID)
	return err
}

// saveReaction saves a reaction to the reactions table using the normalized message info.
func saveReaction(msg *NormalizedMessage, rm *waE2E.ReactionMessage) error {
	emoji := rm.GetText()
	targetKey := rm.GetKey()
	if targetKey == nil {
		return nil
	}
	messageID := targetKey.GetID()

	// Empty emoji means reaction was removed
	if emoji == "" {
		_, err := messageDB.Exec(`DELETE FROM reactions WHERE message_id = ? AND sender_jid = ?`,
			messageID, msg.SenderJID)
		return err
	}

	// UPSERT: update emoji if sender already reacted
	_, err := messageDB.Exec(`
		INSERT INTO reactions (message_id, chat_jid, sender_jid, sender_name, emoji, timestamp)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(message_id, sender_jid) DO UPDATE SET
			emoji = excluded.emoji,
			timestamp = excluded.timestamp
	`, messageID, msg.ChatJID, msg.SenderJID, msg.PushName, emoji, msg.Timestamp)
	return err
}

// extractMessageContent extracts text and media type from a WhatsApp message.
func extractMessageContent(m *waE2E.Message) (text, mediaType string) {
	meta := extractMessageContentFull(m)
	return meta.Text, meta.MediaType
}

// extractMessageContentFull extracts text and full media metadata from a WhatsApp message.
func extractMessageContentFull(m *waE2E.Message) MessageContent {
	if m == nil {
		return MessageContent{}
	}

	var content MessageContent

	// Helper to extract reply context from ContextInfo
	extractReply := func(ci *waE2E.ContextInfo) {
		if ci == nil {
			return
		}
		stanzaID := ci.GetStanzaID()
		if stanzaID == "" {
			return
		}
		content.Reply = &ReplyContext{
			ID:     stanzaID,
			Sender: ci.GetParticipant(),
		}
		// Extract quoted message text preview
		if qm := ci.GetQuotedMessage(); qm != nil {
			qText, _ := extractMessageContent(qm)
			if len(qText) > 200 {
				qText = qText[:200] + "..."
			}
			content.Reply.Text = qText
		}
	}

	switch {
	case m.GetConversation() != "":
		content.Text = m.GetConversation()
	case m.GetExtendedTextMessage() != nil:
		ext := m.GetExtendedTextMessage()
		content.Text = ext.GetText()
		extractReply(ext.GetContextInfo())
	case m.GetImageMessage() != nil:
		img := m.GetImageMessage()
		content.MediaType = "image"
		content.Text = img.GetCaption()
		content.Media = &MediaMetadata{
			MediaType:     "image",
			MimeType:      img.GetMimetype(),
			MediaKey:      img.GetMediaKey(),
			FileSHA256:    img.GetFileSHA256(),
			FileEncSHA256: img.GetFileEncSHA256(),
			FileLength:    int64(img.GetFileLength()),
			DirectPath:    img.GetDirectPath(),
			URL:           img.GetURL(),
		}
		extractReply(img.GetContextInfo())
	case m.GetVideoMessage() != nil:
		vid := m.GetVideoMessage()
		content.MediaType = "video"
		content.Text = vid.GetCaption()
		content.Media = &MediaMetadata{
			MediaType:     "video",
			MimeType:      vid.GetMimetype(),
			MediaKey:      vid.GetMediaKey(),
			FileSHA256:    vid.GetFileSHA256(),
			FileEncSHA256: vid.GetFileEncSHA256(),
			FileLength:    int64(vid.GetFileLength()),
			DirectPath:    vid.GetDirectPath(),
			URL:           vid.GetURL(),
		}
		extractReply(vid.GetContextInfo())
	case m.GetAudioMessage() != nil:
		aud := m.GetAudioMessage()
		content.MediaType = "audio"
		content.Media = &MediaMetadata{
			MediaType:     "audio",
			MimeType:      aud.GetMimetype(),
			MediaKey:      aud.GetMediaKey(),
			FileSHA256:    aud.GetFileSHA256(),
			FileEncSHA256: aud.GetFileEncSHA256(),
			FileLength:    int64(aud.GetFileLength()),
			DirectPath:    aud.GetDirectPath(),
			URL:           aud.GetURL(),
		}
		extractReply(aud.GetContextInfo())
	case m.GetDocumentMessage() != nil:
		doc := m.GetDocumentMessage()
		content.MediaType = "document"
		content.Text = doc.GetCaption()
		content.Media = &MediaMetadata{
			MediaType:     "document",
			MimeType:      doc.GetMimetype(),
			MediaKey:      doc.GetMediaKey(),
			FileSHA256:    doc.GetFileSHA256(),
			FileEncSHA256: doc.GetFileEncSHA256(),
			FileLength:    int64(doc.GetFileLength()),
			DirectPath:    doc.GetDirectPath(),
			URL:           doc.GetURL(),
		}
		extractReply(doc.GetContextInfo())
	case m.GetStickerMessage() != nil:
		stk := m.GetStickerMessage()
		content.MediaType = "sticker"
		content.Media = &MediaMetadata{
			MediaType:     "sticker",
			MimeType:      stk.GetMimetype(),
			MediaKey:      stk.GetMediaKey(),
			FileSHA256:    stk.GetFileSHA256(),
			FileEncSHA256: stk.GetFileEncSHA256(),
			FileLength:    int64(stk.GetFileLength()),
			DirectPath:    stk.GetDirectPath(),
			URL:           stk.GetURL(),
		}
		extractReply(stk.GetContextInfo())
	case m.GetContactMessage() != nil:
		content.MediaType = "contact"
		content.Text = m.GetContactMessage().GetDisplayName()
	case m.GetLocationMessage() != nil:
		content.MediaType = "location"
		loc := m.GetLocationMessage()
		if loc.GetName() != "" {
			content.Text = loc.GetName()
		} else if loc.GetAddress() != "" {
			content.Text = loc.GetAddress()
		}
	case m.GetReactionMessage() != nil:
		// Reaction messages have their own event type, but can appear in history
		// Text is the emoji, key contains the target message
		content.MediaType = "reaction"
		content.Text = m.GetReactionMessage().GetText()

	// ViewOnce messages wrap the actual content in FutureProofMessage
	case m.GetViewOnceMessage() != nil:
		content = extractViewOnceContent(m.GetViewOnceMessage().GetMessage())
	case m.GetViewOnceMessageV2() != nil:
		content = extractViewOnceContent(m.GetViewOnceMessageV2().GetMessage())
	case m.GetViewOnceMessageV2Extension() != nil:
		content = extractViewOnceContent(m.GetViewOnceMessageV2Extension().GetMessage())

	// Live location shares
	case m.GetLiveLocationMessage() != nil:
		content.MediaType = "live_location"
		loc := m.GetLiveLocationMessage()
		if loc.GetCaption() != "" {
			content.Text = loc.GetCaption()
		}

	// Group invites
	case m.GetGroupInviteMessage() != nil:
		content.MediaType = "group_invite"
		inv := m.GetGroupInviteMessage()
		content.Text = inv.GetGroupName()

	// Polls
	case m.GetPollCreationMessage() != nil:
		content.MediaType = "poll"
		poll := m.GetPollCreationMessage()
		content.Text = poll.GetName()
	case m.GetPollCreationMessageV2() != nil:
		content.MediaType = "poll"
		poll := m.GetPollCreationMessageV2()
		content.Text = poll.GetName()
	case m.GetPollCreationMessageV3() != nil:
		content.MediaType = "poll"
		poll := m.GetPollCreationMessageV3()
		content.Text = poll.GetName()
	case m.GetPollUpdateMessage() != nil:
		content.MediaType = "poll_update"

	// Protocol messages (edits, deletes, etc.)
	case m.GetProtocolMessage() != nil:
		proto := m.GetProtocolMessage()
		switch proto.GetType() {
		case waE2E.ProtocolMessage_REVOKE:
			content.MediaType = "deleted"
			content.Text = "[Message deleted]"
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			// Edited message - extract the new content
			if edited := proto.GetEditedMessage(); edited != nil {
				content = extractMessageContentFull(edited)
			}
		default:
			// Other protocol messages (key distribution, ephemeral settings, etc.)
			content.MediaType = "protocol"
		}

	// Signal protocol key distribution - no user content, skip
	case m.GetSenderKeyDistributionMessage() != nil:
		content.MediaType = "key_distribution"
	case m.GetFastRatchetKeySenderKeyDistributionMessage() != nil:
		content.MediaType = "key_distribution"

	// Metadata-only container - no user content
	case m.GetMessageContextInfo() != nil:
		content.MediaType = "context_info"

	// Ephemeral/disappearing messages - unwrap the inner message
	case m.GetEphemeralMessage() != nil:
		if inner := m.GetEphemeralMessage().GetMessage(); inner != nil {
			content = extractMessageContentFull(inner)
		}

	// Document with caption wrapper - unwrap the inner message
	case m.GetDocumentWithCaptionMessage() != nil:
		if inner := m.GetDocumentWithCaptionMessage().GetMessage(); inner != nil {
			content = extractMessageContentFull(inner)
		}

	// Top-level edited message wrapper (different from ProtocolMessage edit)
	case m.GetEditedMessage() != nil:
		if inner := m.GetEditedMessage().GetMessage(); inner != nil {
			content = extractMessageContentFull(inner)
		}

	// Push-to-talk video (circular video notes)
	case m.GetPtvMessage() != nil:
		vid := m.GetPtvMessage()
		content.MediaType = "video"
		content.Text = vid.GetCaption()
		content.Media = &MediaMetadata{
			MediaType:     "video",
			MimeType:      vid.GetMimetype(),
			MediaKey:      vid.GetMediaKey(),
			FileSHA256:    vid.GetFileSHA256(),
			FileEncSHA256: vid.GetFileEncSHA256(),
			FileLength:    int64(vid.GetFileLength()),
			DirectPath:    vid.GetDirectPath(),
			URL:           vid.GetURL(),
		}
		extractReply(vid.GetContextInfo())

	default:
		// Log unrecognized message types for debugging
		// Use proto reflection to identify the message type
		if m.ProtoReflect().IsValid() {
			fields := m.ProtoReflect()
			fields.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
				if v.IsValid() && fd.Kind() == protoreflect.MessageKind {
					fmt.Fprintf(os.Stderr, "Warning: unhandled message type: %s\n", fd.Name())
					return false // stop after first non-nil field
				}
				return true
			})
		}
	}
	return content
}

// extractViewOnceContent extracts content from a ViewOnce message wrapper.
// Prefixes the media type with "viewonce_" to indicate ephemeral content.
func extractViewOnceContent(inner *waE2E.Message) MessageContent {
	if inner == nil {
		return MessageContent{}
	}
	content := extractMessageContentFull(inner)
	if content.MediaType != "" {
		content.MediaType = "viewonce_" + content.MediaType
	} else {
		content.MediaType = "viewonce"
	}
	return content
}
