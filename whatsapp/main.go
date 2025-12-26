package main

import (
	"context"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"mime"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	_ "modernc.org/sqlite"
	"github.com/mdp/qrterminal/v3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

var (
	// XDG-compliant directory layout:
	// - configDir: ~/.config/jean-claude/whatsapp/ (auth/session state)
	// - dataDir: ~/.local/share/jean-claude/whatsapp/ (user data: messages, media)
	configDir string
	dataDir   string
	client    *whatsmeow.Client
	messageDB *sql.DB
	logger    waLog.Logger
)

func init() {
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Fatal: failed to get home directory: %v\n", err)
		os.Exit(1)
	}
	configDir = filepath.Join(home, ".config", "jean-claude", "whatsapp")
	dataDir = filepath.Join(home, ".local", "share", "jean-claude", "whatsapp")
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	// Initialize logger (quiet by default, verbose with -v)
	verbose := false
	for i, arg := range args {
		if arg == "-v" || arg == "--verbose" {
			verbose = true
			args = append(args[:i], args[i+1:]...)
			break
		}
	}
	if verbose {
		logger = waLog.Stdout("CLI", "DEBUG", true)
	} else {
		logger = waLog.Noop
	}

	// Ensure database is closed on exit
	defer func() {
		if messageDB != nil {
			messageDB.Close()
		}
	}()

	var err error
	switch cmd {
	case "auth":
		err = cmdAuth()
	case "send":
		err = cmdSend(args)
	case "send-file":
		err = cmdSendFile(args)
	case "sync":
		err = cmdSync()
	case "messages":
		err = cmdMessages(args)
	case "contacts":
		err = cmdContacts()
	case "chats":
		err = cmdChats(args)
	case "search":
		err = cmdSearch(args)
	case "participants":
		err = cmdParticipants(args)
	case "refresh":
		err = cmdRefresh()
	case "mark-read":
		err = cmdMarkRead(args)
	case "download":
		err = cmdDownload(args)
	case "status":
		err = cmdStatus()
	case "logout":
		err = cmdLogout()
	case "help", "-h", "--help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", cmd)
		printUsage()
		os.Exit(1)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintln(os.Stderr, `whatsapp-cli - WhatsApp command line interface

Usage:
  whatsapp-cli <command> [options]

Commands:
  auth          Authenticate with WhatsApp (scan QR code)
  send          Send a message: send <phone> <message>
  send-file     Send a file: send-file <phone> <file-path>
  sync          Sync messages from WhatsApp to local database
  messages      List messages from local database
  search        Search message history: search <query>
  contacts      List contacts from local database
  chats         List recent chats
  participants  List group participants: participants <group-jid>
  refresh       Fetch chat/group names from WhatsApp
  mark-read     Mark messages in a chat as read: mark-read <chat-jid>
  download      Download media from a message: download <message-id> [--output path]
  status        Show connection status
  logout        Log out and clear credentials

Options:
  -v, --verbose   Enable verbose logging`)
}

// Initialize WhatsApp client
func initClient(ctx context.Context) error {
	if err := os.MkdirAll(configDir, 0700); err != nil {
		return fmt.Errorf("failed to create config directory: %w", err)
	}

	// Migration: rename old whatsapp.db to session.db if needed
	oldSessionPath := filepath.Join(configDir, "whatsapp.db")
	newSessionPath := filepath.Join(configDir, "session.db")
	if _, err := os.Stat(oldSessionPath); err == nil {
		if _, err := os.Stat(newSessionPath); os.IsNotExist(err) {
			if err := os.Rename(oldSessionPath, newSessionPath); err != nil {
				fmt.Fprintf(os.Stderr, "Warning: failed to migrate session database: %v\n", err)
			} else {
				fmt.Fprintln(os.Stderr, "Migrated session database to new location")
			}
		}
	}

	// Session/device state goes in config (auth credential)
	dbPath := newSessionPath
	container, err := sqlstore.New(ctx, "sqlite", "file:"+dbPath+"?_pragma=foreign_keys(1)", logger)
	if err != nil {
		return fmt.Errorf("failed to open database: %w", err)
	}

	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			device = container.NewDevice()
		} else {
			return fmt.Errorf("failed to get device: %w", err)
		}
	}

	client = whatsmeow.NewClient(device, logger)
	return nil
}

// Initialize message database
func initMessageDB() error {
	// Messages are user data, stored in XDG data directory
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		return fmt.Errorf("failed to create data directory: %w", err)
	}

	// Migration: move messages.db from config to data directory if needed
	oldMsgPath := filepath.Join(configDir, "messages.db")
	newMsgPath := filepath.Join(dataDir, "messages.db")
	if _, err := os.Stat(oldMsgPath); err == nil {
		if _, err := os.Stat(newMsgPath); os.IsNotExist(err) {
			if err := os.Rename(oldMsgPath, newMsgPath); err != nil {
				fmt.Fprintf(os.Stderr, "Warning: failed to migrate messages database: %v\n", err)
			} else {
				fmt.Fprintln(os.Stderr, "Migrated messages database to new location")
			}
		}
	}

	var err error
	messageDB, err = sql.Open("sqlite", newMsgPath)
	if err != nil {
		return fmt.Errorf("failed to open message database: %w", err)
	}

	// Create tables
	_, err = messageDB.Exec(`
		CREATE TABLE IF NOT EXISTS messages (
			id TEXT PRIMARY KEY,
			chat_jid TEXT NOT NULL,
			sender_jid TEXT NOT NULL,
			sender_name TEXT,
			timestamp INTEGER NOT NULL,
			text TEXT,
			media_type TEXT,
			is_from_me INTEGER NOT NULL,
			created_at INTEGER NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_jid);
		CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

		CREATE TABLE IF NOT EXISTS contacts (
			jid TEXT PRIMARY KEY,
			name TEXT,
			push_name TEXT,
			updated_at INTEGER NOT NULL
		);

		CREATE TABLE IF NOT EXISTS chats (
			jid TEXT PRIMARY KEY,
			name TEXT,
			is_group INTEGER NOT NULL,
			last_message_time INTEGER,
			marked_as_unread INTEGER NOT NULL DEFAULT 0,
			updated_at INTEGER NOT NULL
		);
	`)
	if err != nil {
		return fmt.Errorf("failed to create tables: %w", err)
	}

	// Migration: populate chats from existing messages if chats table is empty
	var chatCount int
	if err := messageDB.QueryRow("SELECT COUNT(*) FROM chats").Scan(&chatCount); err != nil {
		return fmt.Errorf("failed to count chats: %w", err)
	}
	if chatCount == 0 {
		if _, err = messageDB.Exec(`
			INSERT OR IGNORE INTO chats (jid, name, is_group, last_message_time, updated_at)
			SELECT
				chat_jid,
				'',
				CASE WHEN chat_jid LIKE '%@g.us' THEN 1 ELSE 0 END,
				MAX(timestamp),
				strftime('%s', 'now')
			FROM messages
			GROUP BY chat_jid
		`); err != nil {
			return fmt.Errorf("failed to migrate chats: %w", err)
		}
	}

	// Migration: populate contacts from existing messages if contacts table is empty
	var contactCount int
	if err := messageDB.QueryRow("SELECT COUNT(*) FROM contacts").Scan(&contactCount); err != nil {
		return fmt.Errorf("failed to count contacts: %w", err)
	}
	if contactCount == 0 {
		if _, err = messageDB.Exec(`
			INSERT OR IGNORE INTO contacts (jid, name, push_name, updated_at)
			SELECT
				sender_jid,
				'',
				sender_name,
				strftime('%s', 'now')
			FROM messages
			WHERE sender_name IS NOT NULL AND sender_name != ''
			GROUP BY sender_jid
		`); err != nil {
			return fmt.Errorf("failed to migrate contacts: %w", err)
		}
	}

	// Migration: add is_read column to messages if it doesn't exist
	if !hasColumn(messageDB, "messages", "is_read") {
		if _, err = messageDB.Exec(`ALTER TABLE messages ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("failed to add is_read column: %w", err)
		}
		if _, err = messageDB.Exec(`CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(is_read, chat_jid)`); err != nil {
			return fmt.Errorf("failed to create unread index: %w", err)
		}
	}

	// Migration: add marked_as_unread column to chats if it doesn't exist
	if !hasColumn(messageDB, "chats", "marked_as_unread") {
		if _, err = messageDB.Exec(`ALTER TABLE chats ADD COLUMN marked_as_unread INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("failed to add marked_as_unread column: %w", err)
		}
	}

	// Migration: add media metadata columns to messages if they don't exist
	mediaColumns := []string{
		"mime_type_full TEXT",       // Full MIME type (e.g., image/jpeg)
		"media_key BLOB",            // Decryption key
		"file_sha256 BLOB",          // SHA256 hash of decrypted file
		"file_enc_sha256 BLOB",      // SHA256 hash of encrypted file
		"file_length INTEGER",       // File size in bytes
		"direct_path TEXT",          // WhatsApp CDN path
		"media_url TEXT",            // Full download URL
		"media_file_path TEXT",      // Local file path after download
	}
	for _, colDef := range mediaColumns {
		colName := strings.Split(colDef, " ")[0]
		if !hasColumn(messageDB, "messages", colName) {
			if _, err = messageDB.Exec("ALTER TABLE messages ADD COLUMN " + colDef); err != nil {
				return fmt.Errorf("failed to add %s column: %w", colName, err)
			}
		}
	}

	return nil
}

// hasColumn checks if a column exists in a table.
// SAFETY: table parameter must be a trusted literal, not user input.
// SQLite PRAGMA doesn't support parameterized queries.
func hasColumn(db *sql.DB, table, column string) bool {
	rows, err := db.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		return false
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return false
		}
		if name == column {
			return true
		}
	}
	return false
}

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
			fmt.Fprintln(os.Stderr, "(WhatsApp > Settings > Linked Devices > Link a Device)\n")
			qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stderr)
		case "success":
			fmt.Fprintln(os.Stderr, "\nQR code scanned! Completing device registration...")
			// Clean up QR file
			os.Remove(qrFile)
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
	msg := &waProto.Message{
		Conversation: &message,
	}

	// If replying to a message, add context info
	if replyTo != "" {
		contextInfo, err := getQuotedContext(replyTo, jid.String())
		if err != nil {
			return fmt.Errorf("failed to get quoted message: %w", err)
		}
		// Use ExtendedTextMessage for replies (Conversation doesn't support ContextInfo)
		msg = &waProto.Message{
			ExtendedTextMessage: &waProto.ExtendedTextMessage{
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
	if strings.HasPrefix(mimeType, "image/") {
		mediaType = whatsmeow.MediaImage
	} else if strings.HasPrefix(mimeType, "video/") {
		mediaType = whatsmeow.MediaVideo
	} else if strings.HasPrefix(mimeType, "audio/") {
		mediaType = whatsmeow.MediaAudio
	} else {
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
	var msg *waProto.Message

	switch mediaType {
	case whatsmeow.MediaImage:
		msg = &waProto.Message{
			ImageMessage: &waProto.ImageMessage{
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
		msg = &waProto.Message{
			VideoMessage: &waProto.VideoMessage{
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
		msg = &waProto.Message{
			AudioMessage: &waProto.AudioMessage{
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
		msg = &waProto.Message{
			DocumentMessage: &waProto.DocumentMessage{
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

// cmdSync syncs messages from WhatsApp
func cmdSync() error {
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

	// Set up event handler for messages (atomic counter for thread safety)
	var messageCount atomic.Int64
	client.AddEventHandler(func(evt interface{}) {
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
				isGroup := strings.Contains(chatJID, "@g.us")

				// Get unread count from WhatsApp - this is the authoritative source
				unreadCount := int(conv.GetUnreadCount())

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
				incomingCount := 0
				for _, m := range messages {
					// Determine read status:
					// - Messages from self are always read
					// - For incoming messages: unread if within unreadCount, else read
					isRead := m.isFromMe || incomingCount >= unreadCount
					if !m.isFromMe {
						incomingCount++
					}

					if err := saveHistoryMessageWithReadStatus(chatJID, m.msg, isRead); err != nil {
						fmt.Fprintf(os.Stderr, "Failed to save history message: %v\n", err)
					} else {
						messageCount.Add(1)
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
			// Fired when we read messages on another device (e.g., phone)
			// Only process if the action is marking as read (not unread)
			if v.Action != nil && v.Action.GetRead() {
				chatJID := v.JID.String()
				if _, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE chat_jid = ? AND is_read = 0`, chatJID); err != nil {
					fmt.Fprintf(os.Stderr, "Warning: failed to mark chat messages read: %v\n", err)
				}
				// Clear the "marked as unread" flag
				_, _ = messageDB.Exec(`UPDATE chats SET marked_as_unread = 0 WHERE jid = ?`, chatJID)
			}
		}
	})

	if err := client.Connect(); err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}

	// Wait for history sync
	fmt.Fprintln(os.Stderr, "Syncing messages... (press Ctrl+C to stop)")

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	// Wait for initial sync or interrupt
	select {
	case <-sigChan:
	case <-time.After(30 * time.Second):
	}

	// Fetch names for chats that don't have them
	namesUpdated := 0
	type chatToUpdate struct {
		jid     string
		isGroup bool
	}
	var chatsNeedingNames []chatToUpdate

	rows, err := messageDB.Query(`
		SELECT jid, is_group FROM chats
		WHERE name IS NULL OR name = ''
		ORDER BY last_message_time DESC
		LIMIT 50
	`)
	if err == nil {
		for rows.Next() {
			var jid string
			var isGroup int
			if rows.Scan(&jid, &isGroup) == nil {
				chatsNeedingNames = append(chatsNeedingNames, chatToUpdate{jid, isGroup == 1})
			}
		}
		rows.Close()
	}

	// Now update names (with cursor closed)
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

	output := map[string]any{
		"success":        true,
		"messages_saved": messageCount.Load(),
		"names_updated":  namesUpdated,
	}
	return printJSON(output)
}

// cmdMessages lists messages from local database
func cmdMessages(args []string) error {
	if err := initMessageDB(); err != nil {
		return err
	}

	// Parse args
	var chatJID string
	var unreadOnly bool
	limit := 50
	for i := 0; i < len(args); i++ {
		switch {
		case strings.HasPrefix(args[i], "--chat="):
			chatJID = strings.TrimPrefix(args[i], "--chat=")
		case strings.HasPrefix(args[i], "--max-results="):
			fmt.Sscanf(strings.TrimPrefix(args[i], "--max-results="), "%d", &limit)
		case args[i] == "--unread":
			unreadOnly = true
		}
	}

	// Build query with LEFT JOIN to get chat name
	query := `SELECT m.id, m.chat_jid, m.sender_jid, m.sender_name, m.timestamp, m.text, m.media_type, m.is_from_me, m.is_read,
		CASE
			WHEN c.is_group = 1 THEN COALESCE(NULLIF(c.name, ''), '')
			ELSE COALESCE(NULLIF(c.name, ''), ct.name, ct.push_name, '')
		END as chat_name,
		m.mime_type_full, m.file_length, m.media_file_path
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
	defer rows.Close()

	var messages []map[string]any
	for rows.Next() {
		var id, chatJID, senderJID string
		var senderName, text, mediaType, chatName, mimeType, mediaFilePath sql.NullString
		var timestamp int64
		var isFromMe, isRead int
		var fileLength sql.NullInt64

		if err := rows.Scan(&id, &chatJID, &senderJID, &senderName, &timestamp, &text, &mediaType, &isFromMe, &isRead, &chatName, &mimeType, &fileLength, &mediaFilePath); err != nil {
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
		if mimeType.Valid && mimeType.String != "" {
			msg["mime_type_full"] = mimeType.String
		}
		if fileLength.Valid {
			msg["file_length"] = fileLength.Int64
		}
		if mediaFilePath.Valid && mediaFilePath.String != "" {
			msg["media_file_path"] = mediaFilePath.String
		}
		messages = append(messages, msg)
	}

	return printJSON(messages)
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
	defer rows.Close()

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
	query := `
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
			(SELECT COUNT(*) FROM messages m WHERE m.chat_jid = c.jid AND m.is_read = 0 AND m.is_from_me = 0) as unread_count,
			c.marked_as_unread
		FROM chats c
		LEFT JOIN contacts ct ON c.jid = ct.jid`
	if unreadOnly {
		query += `
		WHERE (SELECT COUNT(*) FROM messages m WHERE m.chat_jid = c.jid AND m.is_read = 0 AND m.is_from_me = 0) > 0
		   OR c.marked_as_unread = 1`
	}
	query += `
		ORDER BY c.last_message_time DESC`

	rows, err := messageDB.Query(query)
	if err != nil {
		return fmt.Errorf("failed to query chats: %w", err)
	}
	defer rows.Close()

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
			"is_group": isGroup == 1,
		}
		if name != "" {
			chat["name"] = name
		}
		if lastMessageTime.Valid {
			chat["last_message_time"] = lastMessageTime.Int64
		}
		if unreadCount > 0 || markedAsUnread == 1 {
			chat["unread_count"] = unreadCount
		}
		chats = append(chats, chat)
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

	// Parse args - first non-flag arg is query
	var query string
	limit := 50
	for i := 0; i < len(args); i++ {
		switch {
		case strings.HasPrefix(args[i], "--max-results="):
			fmt.Sscanf(strings.TrimPrefix(args[i], "--max-results="), "%d", &limit)
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
	defer rows.Close()

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

	if !strings.Contains(groupJID, "@g.us") {
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
	rows, err := messageDB.Query(`
		SELECT jid, is_group FROM chats
		WHERE name IS NULL OR name = ''
		ORDER BY last_message_time DESC
		LIMIT 100
	`)
	if err != nil {
		return fmt.Errorf("failed to query chats: %w", err)
	}
	defer rows.Close()

	type chatInfo struct {
		jid     string
		isGroup bool
	}
	var chatsToRefresh []chatInfo
	for rows.Next() {
		var jid string
		var isGroup int
		if err := rows.Scan(&jid, &isGroup); err != nil {
			continue
		}
		chatsToRefresh = append(chatsToRefresh, chatInfo{jid, isGroup == 1})
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
			rows.Close()
			return fmt.Errorf("failed to scan row: %w", err)
		}
		messageIDs = append(messageIDs, id)
		if senderJID == "" {
			senderJID = sender
		}
	}
	rows.Close()

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
				time.Sleep(2 * time.Second)

				// Parse chat JID
				jid, err := types.ParseJID(chatJID)
				if err == nil {
					// For groups, we need the sender JID; for DMs, sender is the chat JID
					var sender types.JID
					if strings.Contains(chatJID, "@g.us") && senderJID != "" {
						sender, _ = types.ParseJID(senderJID)
					} else {
						sender = jid
					}

					// Convert string IDs to MessageID type
					msgIDs := make([]types.MessageID, len(messageIDs))
					for i, id := range messageIDs {
						msgIDs[i] = types.MessageID(id)
					}

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
	if err == sql.ErrNoRows {
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

// Helper functions

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
	defer rows.Close()

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
		if !strings.Contains(m.jid, "@g.us") {
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
func getQuotedContext(messageID, chatJID string) (*waProto.ContextInfo, error) {
	// Look up the message in the database
	var senderJID, text string
	err := messageDB.QueryRow(`
		SELECT sender_jid, text FROM messages
		WHERE id = ? AND chat_jid = ?
	`, messageID, chatJID).Scan(&senderJID, &text)
	if err == sql.ErrNoRows {
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

	return &waProto.ContextInfo{
		StanzaID:      &messageID,
		Participant:   &participantStr,
		QuotedMessage: &waProto.Message{Conversation: &text},
	}, nil
}

func saveMessage(evt *events.Message) error {
	info := evt.Info
	content := extractMessageContentFull(evt.Message)

	// New messages from others are unread; messages from self are read
	// UPSERT: insert new messages, preserve read status if already marked read (MAX prevents read→unread)
	isRead := boolToInt(info.IsFromMe)

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

	_, err := messageDB.Exec(`
		INSERT INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at,
			mime_type_full, media_key, file_sha256, file_enc_sha256, file_length, direct_path, media_url)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
			media_url = COALESCE(excluded.media_url, messages.media_url)
	`, info.ID, info.Chat.String(), info.Sender.String(), info.PushName, info.Timestamp.Unix(),
		content.Text, content.MediaType, boolToInt(info.IsFromMe), isRead, time.Now().Unix(),
		mimeType, mediaKey, fileSHA256, fileEncSHA256, fileLength, directPath, mediaURL)

	if err == nil {
		// Update chat timestamp (best-effort, don't fail message save)
		_ = saveChat(info.Chat.String(), "", info.Chat.Server == types.GroupServer, info.Timestamp.Unix(), false)
	}

	return err
}

// saveHistoryMessageWithReadStatus saves a message from history sync with the specified read status.
// Uses UPSERT with MAX(is_read) to only move messages from unread→read, never backwards.
// This prevents history sync from marking already-read messages as unread.
func saveHistoryMessageWithReadStatus(chatJID string, msg *waWeb.WebMessageInfo, isRead bool) error {
	if msg == nil {
		return nil
	}

	key := msg.GetKey()
	if key == nil {
		return nil
	}

	content := extractMessageContentFull(msg.GetMessage())

	timestamp := int64(msg.GetMessageTimestamp())
	if timestamp == 0 {
		timestamp = time.Now().Unix()
	}

	// Sender is participant (for groups) or remoteJid (for DMs)
	sender := msg.GetParticipant()
	if sender == "" {
		sender = key.GetRemoteJID()
	}

	// Save contact info from message sender (best-effort)
	if pushName := msg.GetPushName(); pushName != "" && sender != "" {
		_ = saveContact(sender, "", pushName)
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

	// UPSERT pattern: insert new messages, update existing ones only to mark as read (never unread).
	// MAX(is_read, excluded.is_read) ensures read status only moves unread→read, never back.
	_, err := messageDB.Exec(`
		INSERT INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at,
			mime_type_full, media_key, file_sha256, file_enc_sha256, file_length, direct_path, media_url)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			is_read = MAX(messages.is_read, excluded.is_read),
			mime_type_full = COALESCE(excluded.mime_type_full, messages.mime_type_full),
			media_key = COALESCE(excluded.media_key, messages.media_key),
			file_sha256 = COALESCE(excluded.file_sha256, messages.file_sha256),
			file_enc_sha256 = COALESCE(excluded.file_enc_sha256, messages.file_enc_sha256),
			file_length = COALESCE(excluded.file_length, messages.file_length),
			direct_path = COALESCE(excluded.direct_path, messages.direct_path),
			media_url = COALESCE(excluded.media_url, messages.media_url)
	`, key.GetID(), chatJID, sender, msg.GetPushName(), timestamp,
		content.Text, content.MediaType, boolToInt(key.GetFromMe()), boolToInt(isRead), time.Now().Unix(),
		mimeType, mediaKey, fileSHA256, fileEncSHA256, fileLength, directPath, mediaURL)

	return err
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

// MediaMetadata holds media information extracted from a WhatsApp message.
// Used for storing download info and later retrieval.
type MediaMetadata struct {
	MediaType    string // image, video, audio, document, sticker, contact, location
	MimeType     string // Full MIME type (e.g., image/jpeg)
	MediaKey     []byte // Decryption key
	FileSHA256   []byte // SHA256 hash of decrypted file
	FileEncSHA256 []byte // SHA256 hash of encrypted file
	FileLength   int64  // File size in bytes
	DirectPath   string // WhatsApp CDN path
	URL          string // Full download URL
}

// extractMessageContent extracts text and media type from a WhatsApp message.
func extractMessageContent(m *waE2E.Message) (text, mediaType string) {
	meta := extractMessageContentFull(m)
	return meta.Text, meta.MediaType
}

// MessageContent holds full message content including media metadata.
type MessageContent struct {
	Text      string
	MediaType string
	Media     *MediaMetadata
}

// extractMessageContentFull extracts text and full media metadata from a WhatsApp message.
func extractMessageContentFull(m *waE2E.Message) MessageContent {
	if m == nil {
		return MessageContent{}
	}

	var content MessageContent

	switch {
	case m.GetConversation() != "":
		content.Text = m.GetConversation()
	case m.GetExtendedTextMessage() != nil:
		content.Text = m.GetExtendedTextMessage().GetText()
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
	}
	return content
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
