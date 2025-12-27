package main

import (
	"context"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
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

	"github.com/mdp/qrterminal/v3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/reflect/protoreflect"
	_ "modernc.org/sqlite"
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
	// Allow override via environment variables (for testing)
	configDir = os.Getenv("WHATSAPP_CONFIG_DIR")
	dataDir = os.Getenv("WHATSAPP_DATA_DIR")

	// Fall back to XDG-compliant defaults
	if configDir == "" || dataDir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			fmt.Fprintf(os.Stderr, "Fatal: failed to get home directory: %v\n", err)
			os.Exit(1)
		}
		if configDir == "" {
			configDir = filepath.Join(home, ".config", "jean-claude", "whatsapp")
		}
		if dataDir == "" {
			dataDir = filepath.Join(home, ".local", "share", "jean-claude", "whatsapp")
		}
	}
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
			_ = messageDB.Close()
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
		printUsage()
		err = fmt.Errorf("unknown command: %s", cmd)
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1) //nolint:gocritic // intentional exit after error
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
		if errors.Is(err, sql.ErrNoRows) {
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
		"mime_type_full TEXT",  // Full MIME type (e.g., image/jpeg)
		"media_key BLOB",       // Decryption key
		"file_sha256 BLOB",     // SHA256 hash of decrypted file
		"file_enc_sha256 BLOB", // SHA256 hash of encrypted file
		"file_length INTEGER",  // File size in bytes
		"direct_path TEXT",     // WhatsApp CDN path
		"media_url TEXT",       // Full download URL
		"media_file_path TEXT", // Local file path after download
	}
	for _, colDef := range mediaColumns {
		colName := strings.Split(colDef, " ")[0]
		if !hasColumn(messageDB, "messages", colName) {
			if _, err = messageDB.Exec("ALTER TABLE messages ADD COLUMN " + colDef); err != nil {
				return fmt.Errorf("failed to add %s column: %w", colName, err)
			}
		}
	}

	// Migration: add reply context columns to messages if they don't exist
	replyColumns := []string{
		"reply_to_id TEXT",     // ID of message being replied to
		"reply_to_sender TEXT", // Sender of the quoted message
		"reply_to_text TEXT",   // Preview of quoted message text
	}
	for _, colDef := range replyColumns {
		colName := strings.Split(colDef, " ")[0]
		if !hasColumn(messageDB, "messages", colName) {
			if _, err = messageDB.Exec("ALTER TABLE messages ADD COLUMN " + colDef); err != nil {
				return fmt.Errorf("failed to add %s column: %w", colName, err)
			}
		}
	}

	// Create reactions table if it doesn't exist
	_, err = messageDB.Exec(`
		CREATE TABLE IF NOT EXISTS reactions (
			message_id TEXT NOT NULL,
			chat_jid TEXT NOT NULL,
			sender_jid TEXT NOT NULL,
			sender_name TEXT,
			emoji TEXT NOT NULL,
			timestamp INTEGER NOT NULL,
			PRIMARY KEY (message_id, sender_jid)
		);
		CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);
		CREATE INDEX IF NOT EXISTS idx_reactions_chat ON reactions(chat_jid);
	`)
	if err != nil {
		return fmt.Errorf("failed to create reactions table: %w", err)
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
	defer func() { _ = rows.Close() }()
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
		return 0, 0, fmt.Errorf("failed to connect: %w", err)
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

// extractMessageContent extracts text and media type from a WhatsApp message.
func extractMessageContent(m *waE2E.Message) (text, mediaType string) {
	meta := extractMessageContentFull(m)
	return meta.Text, meta.MediaType
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
