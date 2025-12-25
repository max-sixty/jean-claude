package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
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

	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal/v3"
	"github.com/skip2/go-qrcode"
	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/proto/waWeb"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

var (
	dataDir    string
	client     *whatsmeow.Client
	messageDB  *sql.DB
	logger     waLog.Logger
)

func init() {
	// Store in same location as other jean-claude credentials
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Fatal: failed to get home directory: %v\n", err)
		os.Exit(1)
	}
	dataDir = filepath.Join(home, ".config", "jean-claude", "whatsapp")
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
	case "sync":
		err = cmdSync()
	case "messages":
		err = cmdMessages(args)
	case "contacts":
		err = cmdContacts()
	case "chats":
		err = cmdChats()
	case "refresh":
		err = cmdRefresh()
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
  auth       Authenticate with WhatsApp (scan QR code)
  send       Send a message: send <phone> <message>
  sync       Sync messages from WhatsApp to local database
  messages   List messages from local database
  contacts   List contacts from local database
  chats      List recent chats
  refresh    Fetch chat/group names from WhatsApp
  status     Show connection status
  logout     Log out and clear credentials

Options:
  -v, --verbose   Enable verbose logging`)
}

// Initialize WhatsApp client
func initClient(ctx context.Context) error {
	if err := os.MkdirAll(dataDir, 0700); err != nil {
		return fmt.Errorf("failed to create data directory: %w", err)
	}

	dbPath := filepath.Join(dataDir, "whatsapp.db")
	container, err := sqlstore.New(ctx, "sqlite3", "file:"+dbPath+"?_foreign_keys=on", logger)
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
	dbPath := filepath.Join(dataDir, "messages.db")
	var err error
	messageDB, err = sql.Open("sqlite3", dbPath)
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
			unread_count INTEGER NOT NULL DEFAULT 0,
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

	// Migration: add unread_count and marked_as_unread columns to chats if they don't exist
	if !hasColumn(messageDB, "chats", "unread_count") {
		if _, err = messageDB.Exec(`ALTER TABLE chats ADD COLUMN unread_count INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("failed to add unread_count column: %w", err)
		}
	}
	if !hasColumn(messageDB, "chats", "marked_as_unread") {
		if _, err = messageDB.Exec(`ALTER TABLE chats ADD COLUMN marked_as_unread INTEGER NOT NULL DEFAULT 0`); err != nil {
			return fmt.Errorf("failed to add marked_as_unread column: %w", err)
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

	qrFile := filepath.Join(dataDir, "qr.png")

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
	if len(args) < 2 {
		return fmt.Errorf("usage: send <phone> <message>")
	}

	phone := args[0]
	message := strings.Join(args[1:], " ")

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

	// Parse recipient JID
	jid, err := parseJID(phone)
	if err != nil {
		return err
	}

	// Send message
	resp, err := client.SendMessage(ctx, jid, &waProto.Message{
		Conversation: &message,
	})
	if err != nil {
		return fmt.Errorf("failed to send message: %w", err)
	}

	output := map[string]any{
		"success":   true,
		"id":        resp.ID,
		"timestamp": resp.Timestamp.Unix(),
		"recipient": jid.String(),
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

				// Save chat with name and unread count
				if latestTimestamp > 0 || chatName != "" {
					if err := saveChatWithUnread(chatJID, chatName, isGroup, latestTimestamp, unreadCount, conv.GetMarkedAsUnread()); err != nil {
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
		case strings.HasPrefix(args[i], "--limit="):
			fmt.Sscanf(strings.TrimPrefix(args[i], "--limit="), "%d", &limit)
		case args[i] == "--unread":
			unreadOnly = true
		}
	}

	// Build query with LEFT JOIN to get chat name
	query := `SELECT m.id, m.chat_jid, m.sender_jid, m.sender_name, m.timestamp, m.text, m.media_type, m.is_from_me, m.is_read,
		CASE
			WHEN c.is_group = 1 THEN COALESCE(NULLIF(c.name, ''), '')
			ELSE COALESCE(NULLIF(c.name, ''), ct.name, ct.push_name, '')
		END as chat_name
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
		if mediaType.Valid {
			msg["media_type"] = mediaType.String
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
func cmdChats() error {
	if err := initMessageDB(); err != nil {
		return err
	}

	// Join with contacts to get names for DM chats
	// For groups: use chat name only (don't fall back to sender name)
	// For DMs: try contact name, then sender name from messages
	rows, err := messageDB.Query(`
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
			c.is_group, c.last_message_time, c.unread_count, c.marked_as_unread
		FROM chats c
		LEFT JOIN contacts ct ON c.jid = ct.jid
		ORDER BY c.last_message_time DESC
	`)
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
		"success":      true,
		"chats_found":  len(chatsToRefresh),
		"names_updated": updated,
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

func saveMessage(evt *events.Message) error {
	info := evt.Info

	var text string
	if evt.Message.GetConversation() != "" {
		text = evt.Message.GetConversation()
	} else if evt.Message.GetExtendedTextMessage() != nil {
		text = evt.Message.GetExtendedTextMessage().GetText()
	}

	var mediaType string
	if evt.Message.GetImageMessage() != nil {
		mediaType = "image"
	} else if evt.Message.GetVideoMessage() != nil {
		mediaType = "video"
	} else if evt.Message.GetAudioMessage() != nil {
		mediaType = "audio"
	} else if evt.Message.GetDocumentMessage() != nil {
		mediaType = "document"
	}

	// New messages from others are unread; messages from self are read
	isRead := boolToInt(info.IsFromMe)
	_, err := messageDB.Exec(`
		INSERT OR REPLACE INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, info.ID, info.Chat.String(), info.Sender.String(), info.PushName, info.Timestamp.Unix(), text, mediaType, boolToInt(info.IsFromMe), isRead, time.Now().Unix())

	if err == nil {
		// Update chat (best-effort, don't fail message save)
		_ = saveChat(info.Chat.String(), "", info.Chat.Server == types.GroupServer, info.Timestamp.Unix())
	}

	return err
}

// saveHistoryMessageWithReadStatus saves a message from history sync with the specified read status.
// Uses INSERT OR IGNORE because real-time messages (via saveMessage) have more accurate read status
// and shouldn't be overwritten by history sync data.
func saveHistoryMessageWithReadStatus(chatJID string, msg *waWeb.WebMessageInfo, isRead bool) error {
	if msg == nil {
		return nil
	}

	key := msg.GetKey()
	if key == nil {
		return nil
	}

	var text string
	if m := msg.GetMessage(); m != nil {
		if m.GetConversation() != "" {
			text = m.GetConversation()
		} else if m.GetExtendedTextMessage() != nil {
			text = m.GetExtendedTextMessage().GetText()
		}
	}

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

	_, err := messageDB.Exec(`
		INSERT OR IGNORE INTO messages (id, chat_jid, sender_jid, sender_name, timestamp, text, media_type, is_from_me, is_read, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, key.GetID(), chatJID, sender, msg.GetPushName(), timestamp, text, "", boolToInt(key.GetFromMe()), boolToInt(isRead), time.Now().Unix())

	return err
}

func saveContact(jid, name, pushName string) error {
	_, err := messageDB.Exec(`
		INSERT OR REPLACE INTO contacts (jid, name, push_name, updated_at)
		VALUES (?, ?, ?, ?)
	`, jid, name, pushName, time.Now().Unix())
	return err
}

func saveChat(jid, name string, isGroup bool, lastMessageTime int64) error {
	_, err := messageDB.Exec(`
		INSERT OR REPLACE INTO chats (jid, name, is_group, last_message_time, unread_count, marked_as_unread, updated_at)
		VALUES (?, ?, ?, ?, 0, 0, ?)
	`, jid, name, boolToInt(isGroup), lastMessageTime, time.Now().Unix())
	return err
}

func saveChatWithUnread(jid, name string, isGroup bool, lastMessageTime int64, unreadCount int, markedAsUnread bool) error {
	_, err := messageDB.Exec(`
		INSERT OR REPLACE INTO chats (jid, name, is_group, last_message_time, unread_count, marked_as_unread, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, jid, name, boolToInt(isGroup), lastMessageTime, unreadCount, boolToInt(markedAsUnread), time.Now().Unix())
	return err
}

func markMessageRead(msgID string) error {
	_, err := messageDB.Exec(`UPDATE messages SET is_read = 1 WHERE id = ?`, msgID)
	return err
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
