package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
)

func parseJID(phone string) (types.JID, error) {
	// Remove common formatting
	phone = strings.ReplaceAll(phone, " ", "")
	phone = strings.ReplaceAll(phone, "-", "")
	phone = strings.ReplaceAll(phone, "(", "")
	phone = strings.ReplaceAll(phone, ")", "")
	phone = strings.TrimPrefix(phone, "+")

	if strings.Contains(phone, "@") {
		// Already a JID
		return types.ParseJID(phone)
	}

	// Assume individual contact
	return types.NewJID(phone, types.DefaultUserServer), nil
}

// lookupContactByName looks up a contact by name in the local database.
// Returns an error if no contacts match or if multiple contacts match.
func lookupContactByName(name string) (string, error) {
	// Search for contacts matching the name (case-insensitive)
	// Check both contacts table and chats table for names
	query := `
		SELECT DISTINCT jid, COALESCE(name, push_name, '') as display_name
		FROM (
			SELECT jid, name, push_name FROM contacts
			WHERE name LIKE ? OR push_name LIKE ?
			UNION
			SELECT jid, name, '' as push_name FROM chats
			WHERE name LIKE ? AND is_group = 0
		)
		ORDER BY display_name
	`
	pattern := "%" + name + "%"
	rows, err := messageDB.Query(query, pattern, pattern, pattern)
	if err != nil {
		return "", fmt.Errorf("failed to search contacts: %w", err)
	}
	defer func() { _ = rows.Close() }()

	type match struct {
		jid  string
		name string
	}
	var matches []match
	for rows.Next() {
		var m match
		if err := rows.Scan(&m.jid, &m.name); err != nil {
			return "", fmt.Errorf("failed to scan contact: %w", err)
		}
		// Only include individual contacts (not groups)
		if !strings.HasSuffix(m.jid, "@g.us") {
			matches = append(matches, m)
		}
	}

	if len(matches) == 0 {
		return "", fmt.Errorf("no contact found matching '%s'", name)
	}

	if len(matches) > 1 {
		var suggestions []string
		for _, m := range matches {
			// Extract phone number from JID
			phone := strings.Split(m.jid, "@")[0]
			if m.name != "" {
				suggestions = append(suggestions, fmt.Sprintf("  %s (+%s)", m.name, phone))
			} else {
				suggestions = append(suggestions, fmt.Sprintf("  +%s", phone))
			}
		}
		return "", fmt.Errorf("multiple contacts match '%s':\n%s\nUse a more specific name or phone number", name, strings.Join(suggestions, "\n"))
	}

	// Extract phone number from JID (remove @s.whatsapp.net)
	phone := strings.Split(matches[0].jid, "@")[0]
	return phone, nil
}

// getQuotedContext retrieves context info for replying to a specific message
func getQuotedContext(messageID, chatJID string) (*waE2E.ContextInfo, error) {
	// Look up the message in the database
	var senderJID, text string
	err := messageDB.QueryRow(`
		SELECT sender_jid, text FROM messages
		WHERE id = ? AND chat_jid = ?
	`, messageID, chatJID).Scan(&senderJID, &text)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("message not found: %s", messageID)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to look up message: %w", err)
	}

	// Parse sender JID
	participant, err := types.ParseJID(senderJID)
	if err != nil {
		return nil, fmt.Errorf("invalid sender JID: %w", err)
	}
	participantStr := participant.String()

	return &waE2E.ContextInfo{
		StanzaID:      &messageID,
		Participant:   &participantStr,
		QuotedMessage: &waE2E.Message{Conversation: &text},
	}, nil
}

// chatForNameUpdate represents a chat that needs its name fetched/updated
type chatForNameUpdate struct {
	jid     string
	isGroup bool
}

// getChatsNeedingNames returns chats that don't have names cached locally
func getChatsNeedingNames(limit int) ([]chatForNameUpdate, error) {
	rows, err := messageDB.Query(`
		SELECT jid, is_group FROM chats
		WHERE name IS NULL OR name = ''
		ORDER BY last_message_time DESC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	var chats []chatForNameUpdate
	for rows.Next() {
		var jid string
		var isGroup int
		if err := rows.Scan(&jid, &isGroup); err != nil {
			continue
		}
		chats = append(chats, chatForNameUpdate{jid, isGroup == 1})
	}
	return chats, rows.Err()
}

// getChatName returns the name for a chat, fetching from WhatsApp if not cached
func getChatName(ctx context.Context, chatJID string, isGroup bool) string {
	// Check if we already have a name in DB
	var existingName string
	err := messageDB.QueryRow("SELECT name FROM chats WHERE jid = ? AND name IS NOT NULL AND name != ''", chatJID).Scan(&existingName)
	if err == nil && existingName != "" {
		return existingName
	}

	// Need to fetch from WhatsApp
	jid, err := types.ParseJID(chatJID)
	if err != nil {
		return ""
	}

	var name string
	if isGroup {
		groupInfo, err := client.GetGroupInfo(ctx, jid)
		if err == nil && groupInfo.Name != "" {
			name = groupInfo.Name
		}
	} else {
		contact, err := client.Store.Contacts.GetContact(ctx, jid)
		if err == nil {
			if contact.FullName != "" {
				name = contact.FullName
			} else if contact.PushName != "" {
				name = contact.PushName
			}
		}
	}

	return name
}

// getExtensionFromMime returns a file extension for a MIME type
func getExtensionFromMime(mimeType string) string {
	switch mimeType {
	case "image/jpeg":
		return ".jpg"
	case "image/png":
		return ".png"
	case "image/gif":
		return ".gif"
	case "image/webp":
		return ".webp"
	case "video/mp4":
		return ".mp4"
	case "audio/ogg", "audio/ogg; codecs=opus":
		return ".ogg"
	case "audio/mpeg":
		return ".mp3"
	case "application/pdf":
		return ".pdf"
	default:
		if strings.HasPrefix(mimeType, "image/") {
			return ".bin"
		}
		if strings.HasPrefix(mimeType, "video/") {
			return ".mp4"
		}
		if strings.HasPrefix(mimeType, "audio/") {
			return ".ogg"
		}
		return ".bin"
	}
}

// mediaTypeToWA converts our media type string to whatsmeow MediaType and mmsType
func mediaTypeToWA(mediaType string) (whatsmeow.MediaType, string) {
	switch mediaType {
	case "image":
		return whatsmeow.MediaImage, "image"
	case "video":
		return whatsmeow.MediaVideo, "video"
	case "audio":
		return whatsmeow.MediaAudio, "audio"
	case "document":
		return whatsmeow.MediaDocument, "document"
	case "sticker":
		return whatsmeow.MediaImage, "image" // Stickers use image type
	default:
		return whatsmeow.MediaImage, "image"
	}
}

// openFile opens a file with the system's default application
func openFile(path string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", path)
	case "linux":
		cmd = exec.Command("xdg-open", path)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
	default:
		return
	}
	if err := cmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to open file: %v\n", err)
	}
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func printJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
