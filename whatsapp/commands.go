package main

import (
	"context"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"mime"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/mdp/qrterminal/v3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/appstate"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// cmdAuth handles QR code authentication
func cmdAuth() error {
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID != nil {
		fmt.Fprintln(os.Stderr, "Already authenticated. Use 'logout' to clear credentials.")
		return nil
	}

	// Channel to signal when pairing is complete
	pairComplete := make(chan struct{})

	// Add event handler to detect when pairing is truly complete
	client.AddEventHandler(func(evt interface{}) {
		switch evt.(type) {
		case *events.PairSuccess:
			fmt.Fprintln(os.Stderr, "Device paired successfully!")
		case *events.Connected:
			fmt.Fprintln(os.Stderr, "Connected to WhatsApp!")
			close(pairComplete)
		}
	})

	qrChan, _ := client.GetQRChannel(ctx)
	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}

	qrFile := filepath.Join(configDir, "qr.png")

	for evt := range qrChan {
		switch evt.Event {
		case "code":
			// Save QR code to PNG file
			if err := qrcode.WriteFile(evt.Code, qrcode.Medium, 256, qrFile); err != nil {
				fmt.Fprintf(os.Stderr, "Warning: failed to save QR code image: %v\n", err)
			} else {
				fmt.Fprintf(os.Stderr, "QR code saved to: %s\n", qrFile)
				// Open the file with system viewer
				openFile(qrFile)
			}
			// Also print to terminal as fallback
			fmt.Fprintln(os.Stderr, "\nScan this QR code with WhatsApp:")
			fmt.Fprintln(os.Stderr, "(WhatsApp > Settings > Linked Devices > Link a Device)")
			qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stderr)
		case "success":
			fmt.Fprintln(os.Stderr, "\nQR code scanned! Completing device registration...")
			// Clean up QR file
			_ = os.Remove(qrFile)
			// Wait for the Connected event or timeout
			fmt.Fprintln(os.Stderr, "Waiting for device sync to complete...")
			select {
			case <-pairComplete:
				fmt.Fprintln(os.Stderr, "Device registration complete!")
			case <-time.After(60 * time.Second):
				fmt.Fprintln(os.Stderr, "Warning: Timed out waiting for connection, but auth may still be valid")
			}
			client.Disconnect()
			return nil
		case "timeout":
			client.Disconnect()
			return fmt.Errorf("QR code timed out")
		}
	}

	return nil
}

// cmdSend sends a message
func cmdSend(args []string) error {
	// Parse args: send [--name] [--reply-to=ID] <recipient> <message...>
	var name string
	var replyTo string
	var positionalArgs []string

	for i := 0; i < len(args); i++ {
		switch {
		case args[i] == "--name" && i+1 < len(args):
			name = args[i+1]
			i++ // skip next arg
		case strings.HasPrefix(args[i], "--name="):
			name = strings.TrimPrefix(args[i], "--name=")
		case strings.HasPrefix(args[i], "--reply-to="):
			replyTo = strings.TrimPrefix(args[i], "--reply-to=")
		default:
			positionalArgs = append(positionalArgs, args[i])
		}
	}

	if len(positionalArgs) < 1 && name == "" {
		return fmt.Errorf("usage: send [--name=NAME | <phone>] [--reply-to=MSG_ID] <message>")
	}

	var phone string
	var message string

	if name != "" {
		// --name mode: first positional is message
		if len(positionalArgs) < 1 {
			return fmt.Errorf("usage: send --name=NAME [--reply-to=MSG_ID] <message>")
		}
		message = strings.Join(positionalArgs, " ")
	} else {
		// Normal mode: first positional is phone, rest is message
		if len(positionalArgs) < 2 {
			return fmt.Errorf("usage: send <phone> [--reply-to=MSG_ID] <message>")
		}
		phone = positionalArgs[0]
		message = strings.Join(positionalArgs[1:], " ")
	}

	ctx := context.Background()

	// If --name provided, look up contact first (before connecting to WhatsApp)
	if name != "" {
		if err := initMessageDB(); err != nil {
			return err
		}
		var err error
		phone, err = lookupContactByName(name)
		if err != nil {
			return err
		}
	}

	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID == nil {
		return fmt.Errorf("not authenticated. Run 'auth' first")
	}

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer client.Disconnect()

	// Wait for connection
	time.Sleep(2 * time.Second)

	// Parse recipient JID
	jid, err := parseJID(phone)
	if err != nil {
		return err
	}

	// Build message
	msg := &waE2E.Message{
		Conversation: &message,
	}

	// If replying to a message, add context info
	if replyTo != "" {
		contextInfo, err := getQuotedContext(replyTo, jid.String())
		if err != nil {
			return fmt.Errorf("failed to get quoted message: %w", err)
		}
		// Use ExtendedTextMessage for replies (Conversation doesn't support ContextInfo)
		msg = &waE2E.Message{
			ExtendedTextMessage: &waE2E.ExtendedTextMessage{
				Text:        &message,
				ContextInfo: contextInfo,
			},
		}
	}

	// Send message
	resp, err := client.SendMessage(ctx, jid, msg)
	if err != nil {
		return fmt.Errorf("failed to send message: %w", err)
	}

	output := map[string]any{
		"success":   true,
		"id":        resp.ID,
		"timestamp": resp.Timestamp.Unix(),
		"recipient": jid.String(),
	}
	if replyTo != "" {
		output["reply_to"] = replyTo
	}
	return printJSON(output)
}

// cmdSendFile sends a file attachment
func cmdSendFile(args []string) error {
	// Parse args: send-file [--name=NAME] <recipient> <file-path>
	var name string
	var positionalArgs []string

	for i := 0; i < len(args); i++ {
		switch {
		case args[i] == "--name" && i+1 < len(args):
			name = args[i+1]
			i++ // skip next arg
		case strings.HasPrefix(args[i], "--name="):
			name = strings.TrimPrefix(args[i], "--name=")
		default:
			positionalArgs = append(positionalArgs, args[i])
		}
	}

	var phone string
	var filePath string

	if name != "" {
		// --name mode: only file path needed
		if len(positionalArgs) < 1 {
			return fmt.Errorf("usage: send-file --name=NAME <file-path>")
		}
		filePath = positionalArgs[0]
	} else {
		// Normal mode: phone and file path
		if len(positionalArgs) < 2 {
			return fmt.Errorf("usage: send-file <phone> <file-path>")
		}
		phone = positionalArgs[0]
		filePath = positionalArgs[1]
	}

	// If --name provided, look up contact first
	if name != "" {
		if err := initMessageDB(); err != nil {
			return err
		}
		var err error
		phone, err = lookupContactByName(name)
		if err != nil {
			return err
		}
	}

	// Read file
	data, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("failed to read file: %w", err)
	}

	// Detect MIME type from extension
	ext := filepath.Ext(filePath)
	mimeType := mime.TypeByExtension(ext)
	if mimeType == "" {
		mimeType = "application/octet-stream"
	}

	// Determine media type for upload
	var mediaType whatsmeow.MediaType
	switch {
	case strings.HasPrefix(mimeType, "image/"):
		mediaType = whatsmeow.MediaImage
	case strings.HasPrefix(mimeType, "video/"):
		mediaType = whatsmeow.MediaVideo
	case strings.HasPrefix(mimeType, "audio/"):
		mediaType = whatsmeow.MediaAudio
	default:
		mediaType = whatsmeow.MediaDocument
	}

	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID == nil {
		return fmt.Errorf("not authenticated. Run 'auth' first")
	}

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer client.Disconnect()

	// Wait for connection
	time.Sleep(2 * time.Second)

	// Upload file to WhatsApp servers
	uploadResp, err := client.Upload(ctx, data, mediaType)
	if err != nil {
		return fmt.Errorf("failed to upload file: %w", err)
	}

	// Parse recipient JID
	jid, err := parseJID(phone)
	if err != nil {
		return err
	}

	// Build message based on media type
	fileName := filepath.Base(filePath)
	fileLen := uint64(len(data))
	var msg *waE2E.Message

	switch mediaType {
	case whatsmeow.MediaImage:
		msg = &waE2E.Message{
			ImageMessage: &waE2E.ImageMessage{
				URL:           &uploadResp.URL,
				DirectPath:    &uploadResp.DirectPath,
				MediaKey:      uploadResp.MediaKey,
				Mimetype:      &mimeType,
				FileEncSHA256: uploadResp.FileEncSHA256,
				FileSHA256:    uploadResp.FileSHA256,
				FileLength:    &fileLen,
			},
		}
	case whatsmeow.MediaVideo:
		msg = &waE2E.Message{
			VideoMessage: &waE2E.VideoMessage{
				URL:           &uploadResp.URL,
				DirectPath:    &uploadResp.DirectPath,
				MediaKey:      uploadResp.MediaKey,
				Mimetype:      &mimeType,
				FileEncSHA256: uploadResp.FileEncSHA256,
				FileSHA256:    uploadResp.FileSHA256,
				FileLength:    &fileLen,
			},
		}
	case whatsmeow.MediaAudio:
		msg = &waE2E.Message{
			AudioMessage: &waE2E.AudioMessage{
				URL:           &uploadResp.URL,
				DirectPath:    &uploadResp.DirectPath,
				MediaKey:      uploadResp.MediaKey,
				Mimetype:      &mimeType,
				FileEncSHA256: uploadResp.FileEncSHA256,
				FileSHA256:    uploadResp.FileSHA256,
				FileLength:    &fileLen,
			},
		}
	default:
		msg = &waE2E.Message{
			DocumentMessage: &waE2E.DocumentMessage{
				URL:           &uploadResp.URL,
				DirectPath:    &uploadResp.DirectPath,
				MediaKey:      uploadResp.MediaKey,
				Mimetype:      &mimeType,
				FileEncSHA256: uploadResp.FileEncSHA256,
				FileSHA256:    uploadResp.FileSHA256,
				FileLength:    &fileLen,
				FileName:      &fileName,
			},
		}
	}

	// Send message
	resp, err := client.SendMessage(ctx, jid, msg)
	if err != nil {
		return fmt.Errorf("failed to send file: %w", err)
	}

	output := map[string]any{
		"success":   true,
		"id":        resp.ID,
		"timestamp": resp.Timestamp.Unix(),
		"recipient": jid.String(),
		"file":      fileName,
		"size":      fileLen,
		"mime_type": mimeType,
	}
	return printJSON(output)
}

// doSync performs the core sync operation: connects to WhatsApp, receives pushed
// events, and saves them to the local database. Returns sync statistics.
// Requires initClient and initMessageDB to be called first.
func doSync(ctx context.Context) (messagesSaved int64, namesUpdated int, err error) {
	if client.Store.ID == nil {
		return 0, 0, fmt.Errorf("not authenticated. Run 'auth' first")
	}

	// Idle detection for sync completion.
	//
	// WhatsApp's protocol is push-based: we can't request "messages since X".
	// On connect, WhatsApp pushes events (messages, receipts, history) and we
	// save whatever arrives. The challenge is knowing when sync is "done".
	//
	// We use idle detection: track when events last arrived, exit after silence.
	// Events arrive in bursts (typically <100ms gaps), so 500ms of silence
	// reliably indicates completion. This gives ~1-2s total sync time vs 30s
	// with a fixed timeout.
	var messageCount atomic.Int64
	var lastActivity atomic.Int64
	lastActivity.Store(time.Now().UnixNano())

	client.AddEventHandler(func(evt interface{}) {
		lastActivity.Store(time.Now().UnixNano()) // Update on ANY event for idle detection
		switch v := evt.(type) {
		case *events.Message:
			if err := saveMessage(v); err != nil {
				fmt.Fprintf(os.Stderr, "Failed to save message: %v\n", err)
			} else {
				messageCount.Add(1)
			}
		case *events.HistorySync:
			for _, conv := range v.Data.Conversations {
				chatJID := conv.GetID()
				isGroup := strings.HasSuffix(chatJID, "@g.us")

				// Get unread count from WhatsApp - this is the authoritative source
				unreadCount := int(conv.GetUnreadCount())

				// If unreadCount is 0, mark ALL existing messages in this chat as read.
				// This handles the case where messages were marked read on the phone before sync.
				// The MAX(is_read, excluded.is_read) in saveHistoryMessage prevents us from
				// downgrading read status, so we need to explicitly update here.
				if unreadCount == 0 && !conv.GetMarkedAsUnread() {
					if _, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE chat_jid = ? AND is_read = 0`, chatJID); err != nil {
						fmt.Fprintf(os.Stderr, "Warning: failed to mark chat messages read during history sync: %v\n", err)
					}
				}

				// Track most recent message timestamp for this conversation
				var latestTimestamp int64

				// Collect messages sorted by timestamp (newest first) to mark unread correctly
				type msgInfo struct {
					msg       *waWeb.WebMessageInfo
					timestamp int64
					isFromMe  bool
				}
				var messages []msgInfo

				for _, msg := range conv.Messages {
					if m := msg.Message; m != nil {
						ts := int64(m.GetMessageTimestamp())
						isFromMe := m.GetKey().GetFromMe()
						messages = append(messages, msgInfo{m, ts, isFromMe})
						if ts > latestTimestamp {
							latestTimestamp = ts
						}
					}
				}

				// Sort by timestamp descending (newest first) - required for unread tracking below
				sort.Slice(messages, func(i, j int) bool {
					return messages[i].timestamp > messages[j].timestamp
				})

				// Mark the N most recent incoming messages as unread based on WhatsApp's unreadCount.
				// Messages from self are always read. For incoming messages, we count through
				// the sorted list: the first unreadCount incoming messages are unread.
				// Only count messages that are actually saved (not reactions or protocol messages).
				incomingCount := 0
				for _, m := range messages {
					// Determine read status:
					// - Messages from self are always read
					// - For incoming messages: unread if within unreadCount, else read
					isRead := m.isFromMe || incomingCount >= unreadCount

					saved, err := saveHistoryMessageWithReadStatus(chatJID, m.msg, isRead)
					if err != nil {
						fmt.Fprintf(os.Stderr, "Failed to save history message: %v\n", err)
					} else if saved {
						messageCount.Add(1)
						// Only count saved incoming messages toward unread budget
						if !m.isFromMe {
							incomingCount++
						}
					}
				}

				// Get chat name (from DB cache or fetch from WhatsApp)
				chatName := getChatName(ctx, chatJID, isGroup)

				// Save chat with name (unread_count computed from messages table)
				if latestTimestamp > 0 || chatName != "" {
					if err := saveChat(chatJID, chatName, isGroup, latestTimestamp, conv.GetMarkedAsUnread()); err != nil {
						fmt.Fprintf(os.Stderr, "Warning: failed to save chat %s: %v\n", chatJID, err)
					}
				}
			}
		case *events.PushName:
			if err := saveContact(v.JID.String(), "", v.NewPushName); err != nil {
				fmt.Fprintf(os.Stderr, "Warning: failed to save contact: %v\n", err)
			}
		case *events.Receipt:
			// Mark messages as read when we receive read receipts
			if v.Type == types.ReceiptTypeRead || v.Type == types.ReceiptTypeReadSelf {
				for _, msgID := range v.MessageIDs {
					if err := markMessageRead(msgID); err != nil {
						fmt.Fprintf(os.Stderr, "Warning: failed to mark message read: %v\n", err)
					}
				}
			}
		case *events.MarkChatAsRead:
			// Fired when we read messages on another device (e.g., phone) or from app state sync.
			// v.Action.GetRead() returns true if the chat was marked as read, false if marked as unread.
			chatJID := v.JID.String()
			if v.Action != nil && v.Action.GetRead() {
				// Mark all messages in this chat as read
				if _, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE chat_jid = ? AND is_read = 0`, chatJID); err != nil {
					fmt.Fprintf(os.Stderr, "Warning: failed to mark chat messages read: %v\n", err)
				}
				// Clear the "marked as unread" flag
				_, _ = messageDB.Exec(`UPDATE chats SET marked_as_unread = 0 WHERE jid = ?`, chatJID)
			}
			// Note: read:false means "mark as unread" - we don't need to do anything since
			// messages are already unread by default when they arrive.
		}
	})

	if err := client.Connect(); err != nil {
		return 0, 0, fmt.Errorf("failed to connect: %w", err)
	}

	// Fetch read status from app state. WAPatchRegularLow contains MarkChatAsRead
	// mutations that tell us which chats have been explicitly marked as read/unread.
	// This syncs read status for chats where the user has explicitly interacted.
	//
	// Note: WhatsApp only tracks explicit "mark as read/unread" actions in app state,
	// not implicit reading (viewing messages). For chats without explicit markers,
	// we rely on HistorySync unreadCount or user's manual mark-read commands.
	if err := client.FetchAppState(ctx, appstate.WAPatchRegularLow, true, false); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to fetch app state: %v\n", err)
	}

	// Idle-based sync completion.
	//
	// Timing rationale:
	// - 500ms idle timeout: Events arrive in tight bursts. 500ms of silence means
	//   WhatsApp is done sending. Tested values: 100ms works but aggressive,
	//   500ms is safe with margin for network jitter.
	// - 100ms poll interval: Frequent enough to exit promptly after idle threshold.
	// - 60s max wait: Safety cap for first sync after pairing (can have large
	//   history). Normal syncs complete in 1-2s via idle detection.
	//
	// Why not request-based sync? WhatsApp multidevice protocol doesn't support
	// "fetch messages since timestamp X". We must connect, receive whatever
	// WhatsApp pushes, and infer completion from silence.
	fmt.Fprintln(os.Stderr, "Syncing messages...")

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	const (
		idleTimeout  = 500 * time.Millisecond // Exit after this much silence
		pollInterval = 100 * time.Millisecond // How often to check for idle
		maxSyncTime  = 60 * time.Second       // Safety cap (first sync can be slow)
	)
	maxWait := time.After(maxSyncTime)
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

SyncLoop:
	for {
		select {
		case <-sigChan:
			break SyncLoop
		case <-maxWait:
			break SyncLoop
		case <-ticker.C:
			if time.Since(time.Unix(0, lastActivity.Load())) > idleTimeout {
				break SyncLoop
			}
		}
	}

	// Fetch names for chats that don't have them
	chatsNeedingNames, _ := getChatsNeedingNames(50)
	for _, chat := range chatsNeedingNames {
		name := getChatName(ctx, chat.jid, chat.isGroup)
		if name != "" {
			_, err := messageDB.Exec(`UPDATE chats SET name = ?, updated_at = ? WHERE jid = ?`,
				name, time.Now().Unix(), chat.jid)
			if err == nil {
				namesUpdated++
				fmt.Fprintf(os.Stderr, "  %s -> %s\n", chat.jid, name)
			}
		}
	}

	client.Disconnect()

	return messageCount.Load(), namesUpdated, nil
}

func cmdSync() error {
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}
	if err := initMessageDB(); err != nil {
		return err
	}

	messagesSaved, namesUpdated, err := doSync(ctx)
	if err != nil {
		return err
	}

	output := map[string]any{
		"success":        true,
		"messages_saved": messagesSaved,
		"names_updated":  namesUpdated,
	}
	return printJSON(output)
}

// cmdMessages lists messages from local database.
// When --unread is specified, auto-syncs with WhatsApp first to ensure fresh data.
// When --with-media is specified, auto-downloads image media and returns file paths.
func cmdMessages(args []string) error {
	// Parse args first to check if we need to sync
	var chatJID string
	var unreadOnly bool
	var withMedia bool
	limit := 50
	for i := 0; i < len(args); i++ {
		switch {
		case strings.HasPrefix(args[i], "--chat="):
			chatJID = strings.TrimPrefix(args[i], "--chat=")
		case strings.HasPrefix(args[i], "--max-results="):
			_, _ = fmt.Sscanf(strings.TrimPrefix(args[i], "--max-results="), "%d", &limit)
		case args[i] == "--unread":
			unreadOnly = true
		case args[i] == "--with-media":
			withMedia = true
		}
	}

	// --unread implies --with-media for full context when reviewing inbox
	if unreadOnly {
		withMedia = true
	}

	ctx := context.Background()
	if err := initMessageDB(); err != nil {
		return err
	}

	// Check data status (will be included in output if there are issues)
	var dataStatus DataStatus
	if !unreadOnly {
		// Only check/warn if not syncing - --unread will sync first anyway
		dataStatus = getDataStatus()
	}

	// Auto-sync when checking unread messages to ensure fresh data
	if unreadOnly {
		if err := initClient(ctx); err != nil {
			return err
		}
		if _, _, err := doSync(ctx); err != nil {
			return err
		}
	}

	// Build query with LEFT JOIN to get chat name, including reply context
	query := `SELECT m.id, m.chat_jid, m.sender_jid, m.sender_name, m.timestamp, m.text, m.media_type, m.is_from_me, m.is_read,
		CASE
			WHEN c.is_group = 1 THEN COALESCE(NULLIF(c.name, ''), '')
			ELSE COALESCE(NULLIF(c.name, ''), ct.name, ct.push_name, '')
		END as chat_name,
		m.mime_type_full, m.file_length, m.media_file_path,
		m.reply_to_id, m.reply_to_sender, m.reply_to_text,
		m.media_key, m.file_sha256, m.file_enc_sha256, m.direct_path
		FROM messages m
		LEFT JOIN chats c ON m.chat_jid = c.jid
		LEFT JOIN contacts ct ON m.chat_jid = ct.jid`
	var queryArgs []interface{}
	var conditions []string

	if chatJID != "" {
		conditions = append(conditions, "m.chat_jid = ?")
		queryArgs = append(queryArgs, chatJID)
	}
	if unreadOnly {
		conditions = append(conditions, "m.is_read = 0 AND m.is_from_me = 0")
	}

	if len(conditions) > 0 {
		query += " WHERE " + strings.Join(conditions, " AND ")
	}
	query += " ORDER BY m.timestamp DESC LIMIT ?"
	queryArgs = append(queryArgs, limit)

	rows, err := messageDB.Query(query, queryArgs...)
	if err != nil {
		return fmt.Errorf("failed to query messages: %w", err)
	}
	defer func() { _ = rows.Close() }()

	// Collect message IDs to query reactions
	var messageIDs []string
	var messages []map[string]any

	for rows.Next() {
		var id, chatJIDVal, senderJID string
		var senderName, text, mediaType, chatName, mimeType, mediaFilePath sql.NullString
		var replyToID, replyToSender, replyToText sql.NullString
		var directPath sql.NullString
		var timestamp int64
		var isFromMe, isRead int
		var fileLength sql.NullInt64
		var mediaKey, fileSHA256, fileEncSHA256 []byte

		if err := rows.Scan(&id, &chatJIDVal, &senderJID, &senderName, &timestamp, &text, &mediaType, &isFromMe, &isRead, &chatName,
			&mimeType, &fileLength, &mediaFilePath,
			&replyToID, &replyToSender, &replyToText,
			&mediaKey, &fileSHA256, &fileEncSHA256, &directPath); err != nil {
			return fmt.Errorf("failed to scan row: %w", err)
		}

		msg := map[string]any{
			"id":         id,
			"chat_jid":   chatJIDVal,
			"sender_jid": senderJID,
			"timestamp":  timestamp,
			"is_from_me": isFromMe == 1,
			"is_read":    isRead == 1,
		}
		if chatName.Valid && chatName.String != "" {
			msg["chat_name"] = chatName.String
		}
		if senderName.Valid {
			msg["sender_name"] = senderName.String
		}
		if text.Valid {
			msg["text"] = text.String
		}
		if mediaType.Valid && mediaType.String != "" {
			msg["media_type"] = mediaType.String
		}
		if mimeType.Valid && mimeType.String != "" {
			msg["mime_type_full"] = mimeType.String
		}
		if fileLength.Valid {
			msg["file_length"] = fileLength.Int64
		}

		// Handle media file path and auto-download
		filePath := ""
		if mediaFilePath.Valid && mediaFilePath.String != "" {
			filePath = mediaFilePath.String
		}

		// Auto-download media if --with-media and not already downloaded
		if withMedia && mediaType.Valid && isDownloadableMedia(mediaType.String) && filePath == "" && len(mediaKey) > 0 {
			downloaded := downloadMediaForMessage(ctx, id, mediaType.String, mimeType.String, mediaKey, fileSHA256, fileEncSHA256, fileLength.Int64, directPath.String)
			if downloaded != "" {
				filePath = downloaded
			}
		}

		if filePath != "" {
			msg["file"] = filePath
		}

		// Add reply context if present
		if replyToID.Valid && replyToID.String != "" {
			replyTo := map[string]any{
				"id": replyToID.String,
			}
			if replyToSender.Valid && replyToSender.String != "" {
				replyTo["sender"] = replyToSender.String
			}
			if replyToText.Valid && replyToText.String != "" {
				replyTo["text"] = replyToText.String
			}
			msg["reply_to"] = replyTo
		}

		messages = append(messages, msg)
		messageIDs = append(messageIDs, id)
	}

	// Query reactions for all messages
	if len(messageIDs) > 0 {
		reactionsByMsg := getReactionsForMessages(messageIDs)
		for _, msg := range messages {
			msgID := msg["id"].(string)
			if reactions, ok := reactionsByMsg[msgID]; ok {
				msg["reactions"] = reactions
			}
		}
	}

	// Include data status warning in output if there are issues
	if dataStatus.Warning != "" {
		output := map[string]any{
			"messages": messages,
			"_status":  dataStatus,
		}
		return printJSON(output)
	}

	return printJSON(messages)
}

// getReactionsForMessages queries reactions for a list of message IDs.
func getReactionsForMessages(messageIDs []string) map[string][]map[string]any {
	if len(messageIDs) == 0 {
		return nil
	}

	// Build IN clause
	placeholders := make([]string, len(messageIDs))
	args := make([]interface{}, len(messageIDs))
	for i, id := range messageIDs {
		placeholders[i] = "?"
		args[i] = id
	}

	query := `SELECT message_id, sender_jid, sender_name, emoji FROM reactions WHERE message_id IN (` + strings.Join(placeholders, ",") + `)`
	rows, err := messageDB.Query(query, args...)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to query reactions: %v\n", err)
		return nil
	}
	defer func() { _ = rows.Close() }()

	result := make(map[string][]map[string]any)
	for rows.Next() {
		var msgID, senderJID string
		var senderName sql.NullString
		var emoji string
		if err := rows.Scan(&msgID, &senderJID, &senderName, &emoji); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: failed to scan reaction: %v\n", err)
			continue
		}
		reaction := map[string]any{
			"emoji":      emoji,
			"sender_jid": senderJID,
		}
		if senderName.Valid && senderName.String != "" {
			reaction["sender_name"] = senderName.String
		}
		result[msgID] = append(result[msgID], reaction)
	}
	return result
}

// isDownloadableMedia returns true if the media type can be auto-downloaded.
// Handles both regular types (image, video) and viewonce variants (viewonce_image).
func isDownloadableMedia(mediaType string) bool {
	// Strip viewonce_ prefix if present
	mt := strings.TrimPrefix(mediaType, "viewonce_")
	switch mt {
	case "image", "video", "audio", "sticker", "document":
		return true
	default:
		return false
	}
}

// downloadMediaForMessage downloads media for a message and returns the file path.
// On failure, logs to stderr and returns empty string.
func downloadMediaForMessage(ctx context.Context, messageID, mediaType, mimeType string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength int64, directPath string) string {
	if len(mediaKey) == 0 || directPath == "" {
		return ""
	}

	// Determine output path
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to get home directory: %v\n", err)
		return ""
	}
	mediaDir := filepath.Join(home, ".local", "share", "jean-claude", "whatsapp", "media")
	if err := os.MkdirAll(mediaDir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to create media directory: %v\n", err)
		return ""
	}

	ext := getExtensionFromMime(mimeType)
	filename := hex.EncodeToString(fileSHA256) + ext
	outputPath := filepath.Join(mediaDir, filename)

	// Check if already exists
	if _, err := os.Stat(outputPath); err == nil {
		// Update message with file path
		_, _ = messageDB.Exec(`UPDATE messages SET media_file_path = ? WHERE id = ?`, outputPath, messageID)
		return outputPath
	}

	// Need client to download
	if client == nil || !client.IsConnected() {
		// Try to initialize and connect
		if err := initClient(ctx); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: failed to initialize client for download: %v\n", err)
			return ""
		}
		if client.Store.ID == nil {
			fmt.Fprintf(os.Stderr, "Warning: not authenticated, cannot download media\n")
			return ""
		}
		if err := client.Connect(); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: failed to connect for download: %v\n", err)
			return ""
		}
		// Wait briefly for connection
		time.Sleep(500 * time.Millisecond)
	}

	// Download using the correct media type
	waMediaType, mmsType := mediaTypeToWA(mediaType)
	data, err := client.DownloadMediaWithPath(ctx, directPath, fileEncSHA256, fileSHA256, mediaKey, int(fileLength), waMediaType, mmsType)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to download media for %s: %v\n", messageID, err)
		return ""
	}

	if err := os.WriteFile(outputPath, data, 0644); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to write media file: %v\n", err)
		return ""
	}

	// Update message with file path
	_, _ = messageDB.Exec(`UPDATE messages SET media_file_path = ? WHERE id = ?`, outputPath, messageID)
	return outputPath
}

// cmdContacts lists contacts from local database
func cmdContacts() error {
	if err := initMessageDB(); err != nil {
		return err
	}

	rows, err := messageDB.Query(`SELECT jid, name, push_name FROM contacts ORDER BY name, push_name`)
	if err != nil {
		return fmt.Errorf("failed to query contacts: %w", err)
	}
	defer func() { _ = rows.Close() }()

	var contacts []map[string]any
	for rows.Next() {
		var jid string
		var name, pushName sql.NullString

		if err := rows.Scan(&jid, &name, &pushName); err != nil {
			return fmt.Errorf("failed to scan row: %w", err)
		}

		contact := map[string]any{"jid": jid}
		if name.Valid {
			contact["name"] = name.String
		}
		if pushName.Valid {
			contact["push_name"] = pushName.String
		}
		contacts = append(contacts, contact)
	}

	return printJSON(contacts)
}

// cmdChats lists chats from local database
func cmdChats(args []string) error {
	if err := initMessageDB(); err != nil {
		return err
	}

	// Check data status and warn if there are issues
	dataStatus := getDataStatus()

	// Parse args
	var unreadOnly bool
	for i := 0; i < len(args); i++ {
		if args[i] == "--unread" {
			unreadOnly = true
		}
	}

	// Join with contacts to get names for DM chats
	// For groups: use chat name only (don't fall back to sender name)
	// For DMs: try contact name, then sender name from messages
	// Compute unread_count from messages table (single source of truth)
	// Use CTE to calculate unread count once, then use it in both SELECT and WHERE
	query := `
		WITH chat_unread AS (
			SELECT chat_jid, COUNT(*) as cnt
			FROM messages
			WHERE is_read = 0 AND is_from_me = 0
			GROUP BY chat_jid
		)
		SELECT c.jid,
			CASE
				WHEN c.is_group = 1 THEN COALESCE(NULLIF(c.name, ''), '')
				ELSE COALESCE(
					NULLIF(c.name, ''),
					ct.name,
					ct.push_name,
					(SELECT m.sender_name FROM messages m
					 WHERE m.chat_jid = c.jid AND length(m.sender_name) > 0
					 ORDER BY m.timestamp DESC LIMIT 1),
					''
				)
			END,
			c.is_group,
			c.last_message_time,
			COALESCE(cu.cnt, 0) as unread_count,
			c.marked_as_unread
		FROM chats c
		LEFT JOIN contacts ct ON c.jid = ct.jid
		LEFT JOIN chat_unread cu ON c.jid = cu.chat_jid`
	if unreadOnly {
		query += `
		WHERE COALESCE(cu.cnt, 0) > 0 OR c.marked_as_unread = 1`
	}
	query += `
		ORDER BY c.last_message_time DESC`

	rows, err := messageDB.Query(query)
	if err != nil {
		return fmt.Errorf("failed to query chats: %w", err)
	}
	defer func() { _ = rows.Close() }()

	var chats []map[string]any
	for rows.Next() {
		var jid string
		var name string
		var isGroup int
		var lastMessageTime sql.NullInt64
		var unreadCount, markedAsUnread int

		if err := rows.Scan(&jid, &name, &isGroup, &lastMessageTime, &unreadCount, &markedAsUnread); err != nil {
			return fmt.Errorf("failed to scan row: %w", err)
		}

		chat := map[string]any{
			"jid":      jid,
			"name":     name, // Always include for consistent schema
			"is_group": isGroup == 1,
		}
		if lastMessageTime.Valid {
			chat["last_message_time"] = lastMessageTime.Int64
		}
		if unreadCount > 0 || markedAsUnread == 1 {
			chat["unread_count"] = unreadCount
		}
		chats = append(chats, chat)
	}

	// Include data status warning in output if there are issues
	if dataStatus.Warning != "" {
		output := map[string]any{
			"chats":   chats,
			"_status": dataStatus,
		}
		return printJSON(output)
	}

	return printJSON(chats)
}

// cmdSearch searches message history
func cmdSearch(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: search <query> [--max-results=N]")
	}

	if err := initMessageDB(); err != nil {
		return err
	}

	// Check data status (will be included in output if there are issues)
	dataStatus := getDataStatus()

	// Parse args - first non-flag arg is query
	var query string
	limit := 50
	for i := 0; i < len(args); i++ {
		switch {
		case strings.HasPrefix(args[i], "--max-results="):
			_, _ = fmt.Sscanf(strings.TrimPrefix(args[i], "--max-results="), "%d", &limit)
		case !strings.HasPrefix(args[i], "--"):
			if query == "" {
				query = args[i]
			}
		}
	}

	if query == "" {
		return fmt.Errorf("usage: search <query> [--max-results=N]")
	}

	// Search messages with LIKE query
	sqlQuery := `SELECT m.id, m.chat_jid, m.sender_jid, m.sender_name, m.timestamp, m.text, m.media_type, m.is_from_me, m.is_read,
		CASE
			WHEN c.is_group = 1 THEN COALESCE(NULLIF(c.name, ''), '')
			ELSE COALESCE(NULLIF(c.name, ''), ct.name, ct.push_name, '')
		END as chat_name
		FROM messages m
		LEFT JOIN chats c ON m.chat_jid = c.jid
		LEFT JOIN contacts ct ON m.chat_jid = ct.jid
		WHERE m.text LIKE ?
		ORDER BY m.timestamp DESC
		LIMIT ?`

	rows, err := messageDB.Query(sqlQuery, "%"+query+"%", limit)
	if err != nil {
		return fmt.Errorf("failed to search messages: %w", err)
	}
	defer func() { _ = rows.Close() }()

	var messages []map[string]any
	for rows.Next() {
		var id, chatJID, senderJID string
		var senderName, text, mediaType, chatName sql.NullString
		var timestamp int64
		var isFromMe, isRead int

		if err := rows.Scan(&id, &chatJID, &senderJID, &senderName, &timestamp, &text, &mediaType, &isFromMe, &isRead, &chatName); err != nil {
			return fmt.Errorf("failed to scan row: %w", err)
		}

		msg := map[string]any{
			"id":         id,
			"chat_jid":   chatJID,
			"sender_jid": senderJID,
			"timestamp":  timestamp,
			"is_from_me": isFromMe == 1,
			"is_read":    isRead == 1,
		}
		if chatName.Valid && chatName.String != "" {
			msg["chat_name"] = chatName.String
		}
		if senderName.Valid {
			msg["sender_name"] = senderName.String
		}
		if text.Valid {
			msg["text"] = text.String
		}
		if mediaType.Valid && mediaType.String != "" {
			msg["media_type"] = mediaType.String
		}
		messages = append(messages, msg)
	}

	// Include data status warning in output if there are issues
	if dataStatus.Warning != "" {
		output := map[string]any{
			"messages": messages,
			"_status":  dataStatus,
		}
		return printJSON(output)
	}

	return printJSON(messages)
}

// cmdParticipants lists group participants
func cmdParticipants(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: participants <group-jid>")
	}

	groupJID := args[0]

	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID == nil {
		return fmt.Errorf("not authenticated. Run 'auth' first")
	}

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer client.Disconnect()

	// Wait for connection
	time.Sleep(2 * time.Second)

	// Parse group JID
	jid, err := types.ParseJID(groupJID)
	if err != nil {
		return fmt.Errorf("invalid group JID: %w", err)
	}

	if !strings.HasSuffix(groupJID, "@g.us") {
		return fmt.Errorf("not a group JID (must end with @g.us)")
	}

	// Get group info
	groupInfo, err := client.GetGroupInfo(ctx, jid)
	if err != nil {
		return fmt.Errorf("failed to get group info: %w", err)
	}

	var participants []map[string]any
	for _, p := range groupInfo.Participants {
		participant := map[string]any{
			"jid": p.JID.String(),
		}
		if p.IsAdmin {
			participant["is_admin"] = true
		}
		if p.IsSuperAdmin {
			participant["is_super_admin"] = true
		}
		// Try to get contact name
		contact, err := client.Store.Contacts.GetContact(ctx, p.JID)
		if err == nil {
			if contact.FullName != "" {
				participant["name"] = contact.FullName
			} else if contact.PushName != "" {
				participant["name"] = contact.PushName
			}
		}
		participants = append(participants, participant)
	}

	output := map[string]any{
		"group_jid":    groupJID,
		"group_name":   groupInfo.Name,
		"participants": participants,
	}
	return printJSON(output)
}

// cmdRefresh fetches chat names from WhatsApp
func cmdRefresh() error {
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}
	if err := initMessageDB(); err != nil {
		return err
	}

	if client.Store.ID == nil {
		return fmt.Errorf("not authenticated. Run 'auth' first")
	}

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer client.Disconnect()

	// Wait for connection
	time.Sleep(2 * time.Second)

	// Get chats without names
	chatsToRefresh, err := getChatsNeedingNames(100)
	if err != nil {
		return fmt.Errorf("failed to query chats: %w", err)
	}

	fmt.Fprintf(os.Stderr, "Refreshing names for %d chats...\n", len(chatsToRefresh))

	updated := 0
	for _, chat := range chatsToRefresh {
		jid, err := types.ParseJID(chat.jid)
		if err != nil {
			continue
		}

		var name string
		if chat.isGroup {
			// Fetch group info from WhatsApp
			groupInfo, err := client.GetGroupInfo(ctx, jid)
			if err == nil && groupInfo.Name != "" {
				name = groupInfo.Name
			}
		} else {
			// Fetch contact info from store
			contact, err := client.Store.Contacts.GetContact(ctx, jid)
			if err == nil && contact.FullName != "" {
				name = contact.FullName
			} else if contact.PushName != "" {
				name = contact.PushName
			}
		}

		if name != "" {
			_, err := messageDB.Exec(`UPDATE chats SET name = ?, updated_at = ? WHERE jid = ?`,
				name, time.Now().Unix(), chat.jid)
			if err == nil {
				updated++
				fmt.Fprintf(os.Stderr, "  %s -> %s\n", chat.jid, name)
			}
		}

		// Rate limit to avoid hitting WhatsApp too hard
		time.Sleep(100 * time.Millisecond)
	}

	output := map[string]any{
		"success":       true,
		"chats_found":   len(chatsToRefresh),
		"names_updated": updated,
	}
	return printJSON(output)
}

// cmdMarkAllRead marks all messages in all chats as read (local only)
func cmdMarkAllRead() error {
	if err := initMessageDB(); err != nil {
		return err
	}

	// Mark all messages as read
	result, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE is_read = 0`)
	if err != nil {
		return fmt.Errorf("failed to mark messages as read: %w", err)
	}
	affected, _ := result.RowsAffected()

	// Clear all "marked as unread" flags
	_, _ = messageDB.Exec(`UPDATE chats SET marked_as_unread = 0 WHERE marked_as_unread = 1`)

	output := map[string]any{
		"success":         true,
		"messages_marked": affected,
	}
	return printJSON(output)
}

// cmdMarkRead marks all messages in a chat as read (local + sends read receipts to WhatsApp)
func cmdMarkRead(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: mark-read <chat-jid>")
	}

	chatJID := args[0]

	if err := initMessageDB(); err != nil {
		return err
	}
	// Get unread message IDs and sender JIDs for sending read receipts
	rows, err := messageDB.Query(`
		SELECT id, sender_jid FROM messages
		WHERE chat_jid = ? AND is_read = 0 AND is_from_me = 0
		ORDER BY timestamp DESC
	`, chatJID)
	if err != nil {
		return fmt.Errorf("failed to query unread messages: %w", err)
	}

	var messageIDs []string
	var senderJID string
	for rows.Next() {
		var id, sender string
		if err := rows.Scan(&id, &sender); err != nil {
			_ = rows.Close()
			return fmt.Errorf("failed to scan row: %w", err)
		}
		messageIDs = append(messageIDs, id)
		if senderJID == "" {
			senderJID = sender
		}
	}
	_ = rows.Close()
	if err := rows.Err(); err != nil {
		return fmt.Errorf("failed to iterate rows: %w", err)
	}

	// Send read receipts to WhatsApp if there are unread messages
	receiptsSent := 0
	if len(messageIDs) > 0 {
		ctx := context.Background()
		if err := initClient(ctx); err != nil {
			return err
		}

		if client.Store.ID != nil {
			if err := client.Connect(); err == nil {
				defer client.Disconnect()
				// Wait for connection to stabilize before sending read receipts
				time.Sleep(2 * time.Second)

				// Parse chat JID
				jid, err := types.ParseJID(chatJID)
				if err == nil {
					// For groups, we need the sender JID; for DMs, sender is the chat JID
					var sender types.JID
					if strings.HasSuffix(chatJID, "@g.us") && senderJID != "" {
						sender, _ = types.ParseJID(senderJID)
					} else {
						sender = jid
					}

					// Convert string IDs to MessageID type
					msgIDs := make([]types.MessageID, len(messageIDs))
					copy(msgIDs, messageIDs)

					// Send read receipt
					if err := client.MarkRead(ctx, msgIDs, time.Now(), jid, sender); err != nil {
						fmt.Fprintf(os.Stderr, "Warning: failed to send read receipts: %v\n", err)
					} else {
						receiptsSent = len(messageIDs)
					}
				}
			}
		}
	}

	// Mark all messages in the chat as read in local DB
	result, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE chat_jid = ? AND is_read = 0`, chatJID)
	if err != nil {
		return fmt.Errorf("failed to mark messages as read: %w", err)
	}

	affected, _ := result.RowsAffected()

	// Clear the "marked as unread" flag if set
	_, _ = messageDB.Exec(`UPDATE chats SET marked_as_unread = 0 WHERE jid = ?`, chatJID)

	output := map[string]any{
		"success":         true,
		"chat_jid":        chatJID,
		"messages_marked": affected,
		"receipts_sent":   receiptsSent,
	}
	return printJSON(output)
}

// cmdDownload downloads media from a message
func cmdDownload(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: download <message-id> [--output path]")
	}

	messageID := args[0]
	var outputPath string
	for i := 1; i < len(args); i++ {
		if strings.HasPrefix(args[i], "--output=") {
			outputPath = strings.TrimPrefix(args[i], "--output=")
		} else if args[i] == "--output" && i+1 < len(args) {
			outputPath = args[i+1]
			i++
		}
	}

	if err := initMessageDB(); err != nil {
		return err
	}

	// Look up message to get media metadata
	var mediaType, mimeType, directPath sql.NullString
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength sql.NullInt64
	var existingPath sql.NullString

	err := messageDB.QueryRow(`
		SELECT media_type, mime_type_full, media_key, file_sha256, file_enc_sha256, file_length, direct_path, media_file_path
		FROM messages WHERE id = ?
	`, messageID).Scan(&mediaType, &mimeType, &mediaKey, &fileSHA256, &fileEncSHA256, &fileLength, &directPath, &existingPath)
	if errors.Is(err, sql.ErrNoRows) {
		return fmt.Errorf("message not found: %s", messageID)
	}
	if err != nil {
		return fmt.Errorf("failed to query message: %w", err)
	}

	// Check if this is a media message
	if !mediaType.Valid || mediaType.String == "" {
		return fmt.Errorf("message has no media")
	}
	if len(mediaKey) == 0 {
		return fmt.Errorf("message has no download metadata (media_key missing)")
	}

	// Check if already downloaded
	if existingPath.Valid && existingPath.String != "" {
		// Verify file still exists
		if _, err := os.Stat(existingPath.String); err == nil {
			output := map[string]any{
				"success":    true,
				"message_id": messageID,
				"file":       existingPath.String,
				"cached":     true,
			}
			return printJSON(output)
		}
	}

	// Determine output path if not specified
	if outputPath == "" {
		// Use XDG data dir: ~/.local/share/jean-claude/whatsapp/media/
		home, _ := os.UserHomeDir()
		mediaDir := filepath.Join(home, ".local", "share", "jean-claude", "whatsapp", "media")
		if err := os.MkdirAll(mediaDir, 0755); err != nil {
			return fmt.Errorf("failed to create media directory: %w", err)
		}

		// Use file hash as filename to deduplicate
		ext := getExtensionFromMime(mimeType.String)
		filename := hex.EncodeToString(fileSHA256) + ext
		outputPath = filepath.Join(mediaDir, filename)

		// Check if file already exists (downloaded via another message with same content)
		if _, err := os.Stat(outputPath); err == nil {
			// Update message with existing file path
			_, _ = messageDB.Exec(`UPDATE messages SET media_file_path = ? WHERE id = ?`, outputPath, messageID)
			output := map[string]any{
				"success":    true,
				"message_id": messageID,
				"file":       outputPath,
				"cached":     true,
			}
			return printJSON(output)
		}
	}

	// Need to connect to WhatsApp to download
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID == nil {
		return fmt.Errorf("not authenticated. Run 'auth' first")
	}

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer client.Disconnect()

	// Wait for connection
	time.Sleep(2 * time.Second)

	// Download using whatsmeow
	waMediaType, mmsType := mediaTypeToWA(mediaType.String)
	data, err := client.DownloadMediaWithPath(
		ctx,
		directPath.String,
		fileEncSHA256,
		fileSHA256,
		mediaKey,
		int(fileLength.Int64),
		waMediaType,
		mmsType,
	)
	if err != nil {
		return fmt.Errorf("failed to download media: %w", err)
	}

	// Write to file
	if err := os.WriteFile(outputPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write file: %w", err)
	}

	// Update message with file path
	_, _ = messageDB.Exec(`UPDATE messages SET media_file_path = ? WHERE id = ?`, outputPath, messageID)

	output := map[string]any{
		"success":    true,
		"message_id": messageID,
		"file":       outputPath,
		"size":       len(data),
		"cached":     false,
	}
	return printJSON(output)
}

// cmdStatus shows connection status
func cmdStatus() error {
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	status := map[string]any{
		"authenticated": client.Store.ID != nil,
		"config_dir":    configDir,
		"data_dir":      dataDir,
	}

	if client.Store.ID != nil {
		status["phone"] = client.Store.ID.User
	}

	return printJSON(status)
}

// cmdLogout clears credentials
func cmdLogout() error {
	ctx := context.Background()
	if err := initClient(ctx); err != nil {
		return err
	}

	if client.Store.ID == nil {
		fmt.Fprintln(os.Stderr, "Not authenticated.")
		return nil
	}

	if err := client.Logout(context.Background()); err != nil {
		// Even if logout fails, clear local data
		fmt.Fprintf(os.Stderr, "Warning: logout request failed: %v\n", err)
	}

	fmt.Fprintln(os.Stderr, "Logged out successfully.")
	return nil
}
