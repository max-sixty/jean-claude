package main

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	"go.mau.fi/whatsmeow"
	waLog "go.mau.fi/whatsmeow/util/log"
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
	case "mark-all-read":
		err = cmdMarkAllRead()
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
  mark-all-read Mark all messages in all chats as read
  download      Download media from a message: download <message-id> [--output path]
  status        Show connection status
  logout        Log out and clear credentials

Options:
  -v, --verbose   Enable verbose logging`)
}
