package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
)

// initClient initializes the WhatsApp client.
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

// initMessageDB initializes the message database.
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
