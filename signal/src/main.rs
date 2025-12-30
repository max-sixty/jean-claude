//! Signal CLI - Command-line interface for Signal Messenger
//!
//! Provides JSON-based CLI for sending/receiving Signal messages,
//! designed for integration with jean-claude.

use std::path::PathBuf;
use std::process::Command as ProcessCommand;
use std::time::UNIX_EPOCH;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use directories::ProjectDirs;
use futures::{channel::oneshot, future, pin_mut, StreamExt};
use presage::libsignal_service::configuration::SignalServers;
use presage::libsignal_service::content::ContentBody;
use presage::libsignal_service::prelude::Uuid;
use presage::libsignal_service::protocol::ServiceId;
use presage::manager::Registered;
use presage::model::identity::OnNewIdentity;
use presage::model::messages::Received;
use presage::proto::{sync_message, DataMessage};
use presage::store::{ContentsStore, Thread};
use presage::Manager;
use presage_store_sqlite::SqliteStore;
use rusqlite::Connection;
use serde::Serialize;
use tracing::{debug, warn};

/// Signal CLI - send and receive Signal messages
#[derive(Parser)]
#[command(name = "signal-cli")]
#[command(about = "Signal Messenger CLI for jean-claude integration")]
struct Cli {
    /// Enable verbose logging
    #[arg(short, long, global = true)]
    verbose: bool,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Link as a secondary device (scan QR code with Signal app)
    Link {
        /// Device name shown in Signal settings
        #[arg(short, long, default_value = "jean-claude")]
        device_name: String,
    },

    /// Show account information
    Whoami,

    /// List chats (contacts and groups combined)
    Chats {
        /// Maximum number of chats to return
        #[arg(short = 'n', long, default_value = "50")]
        max_results: usize,
    },

    /// Send a message (reads message from stdin)
    Send {
        /// Recipient UUID
        recipient: String,
    },

    /// Receive pending messages
    Receive,

    /// List messages from a chat
    Messages {
        /// Chat ID (UUID for contacts, hex for groups)
        chat_id: String,

        /// Maximum number of messages to return
        #[arg(short = 'n', long, default_value = "50")]
        max_results: usize,
    },

    /// Show connection status
    Status,

    /// Mark messages in a chat as read (local only)
    MarkRead {
        /// Chat IDs (UUID for contacts, hex for groups)
        chat_ids: Vec<String>,
    },
}

/// Output types for JSON serialization

#[derive(Serialize)]
struct ChatOutput {
    id: String,
    name: String,
    is_group: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    phone: Option<String>,
}

#[derive(Serialize)]
struct MessageOutput {
    id: String,
    chat_id: String,
    sender: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    sender_name: Option<String>,
    timestamp: i64,
    text: String,
    is_outgoing: bool,
    is_read: bool,
}

#[derive(Serialize)]
struct WhoamiOutput {
    uuid: String,
    phone: Option<String>,
    device_id: u32,
}

#[derive(Serialize)]
struct StatusOutput {
    linked: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    uuid: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    phone: Option<String>,
}

#[derive(Serialize)]
struct SendOutput {
    success: bool,
    timestamp: i64,
}

#[derive(Serialize)]
struct LinkOutput {
    success: bool,
    uuid: String,
    device_name: String,
}

#[derive(Serialize)]
struct MarkReadOutput {
    success: bool,
    chats_marked: usize,
    messages_marked: i64,
}

fn get_data_dir() -> Result<PathBuf> {
    let dirs =
        ProjectDirs::from("", "", "jean-claude").context("Failed to determine data directory")?;
    let data_dir = dirs.data_dir().join("signal");
    std::fs::create_dir_all(&data_dir)?;
    Ok(data_dir)
}

fn get_db_path() -> Result<String> {
    let path = get_data_dir()?.join("signal.db");
    Ok(path.display().to_string())
}

/// Track read sync messages from other devices.
///
/// Uses a separate SQLite database because presage-store-sqlite doesn't expose
/// its connection for custom tables. This tracks when messages were read on
/// other devices (phone), allowing us to show accurate is_read status.
mod read_sync {
    use super::*;

    fn get_read_sync_db_path() -> Result<PathBuf> {
        Ok(get_data_dir()?.join("read_sync.db"))
    }

    pub fn open_read_sync_db() -> Result<Connection> {
        let path = get_read_sync_db_path()?;
        let conn = Connection::open(&path)?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS read_sync (
                sender_aci TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                read_at INTEGER NOT NULL,
                PRIMARY KEY (sender_aci, timestamp)
            )",
            [],
        )?;

        Ok(conn)
    }

    /// Record that a message was read (from SyncMessage.Read)
    fn mark_as_read(conn: &Connection, sender_aci: &str, timestamp: u64) -> rusqlite::Result<()> {
        let now = std::time::SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;

        conn.execute(
            "INSERT OR IGNORE INTO read_sync (sender_aci, timestamp, read_at) VALUES (?1, ?2, ?3)",
            rusqlite::params![sender_aci, timestamp as i64, now],
        )?;

        Ok(())
    }

    /// Check if a message has been read.
    /// Returns false on database errors (safe default: show as unread).
    pub fn is_read(conn: &Connection, sender_aci: &str, timestamp: u64) -> bool {
        conn.query_row(
            "SELECT 1 FROM read_sync WHERE sender_aci = ?1 AND timestamp = ?2",
            rusqlite::params![sender_aci, timestamp as i64],
            |_| Ok(()),
        )
        .is_ok()
    }

    /// Process SyncMessage read entries in a single transaction.
    pub fn process_sync_reads(conn: &mut Connection, reads: &[sync_message::Read]) -> Result<usize> {
        let tx = conn.transaction()?;
        let mut count = 0;

        for read in reads {
            if let (Some(sender_aci), Some(timestamp)) = (&read.sender_aci, read.timestamp) {
                mark_as_read(&tx, sender_aci, timestamp)?;
                count += 1;
            }
        }

        tx.commit()?;
        Ok(count)
    }

    /// Mark all messages from a sender as read.
    pub fn mark_sender_read(conn: &mut Connection, sender_aci: &str, timestamps: &[u64]) -> Result<i64> {
        let tx = conn.transaction()?;
        let mut count = 0i64;

        for &ts in timestamps {
            mark_as_read(&tx, sender_aci, ts)?;
            count += 1;
        }

        tx.commit()?;
        Ok(count)
    }
}

async fn open_store() -> Result<SqliteStore> {
    let db_path = get_db_path()?;
    debug!("Opening store at {}", db_path);

    SqliteStore::open_with_passphrase(&db_path, None, OnNewIdentity::Trust)
        .await
        .context("Failed to open Signal database")
}

async fn load_registered_manager() -> Result<Manager<SqliteStore, Registered>> {
    let store = open_store().await?;
    Manager::load_registered(store)
        .await
        .context("Not linked to Signal. Run 'signal-cli link' first.")
}

async fn cmd_link(device_name: String) -> Result<()> {
    let db_path = get_db_path()?;
    debug!("Linking device, store at {}", db_path);

    let store = SqliteStore::open_with_passphrase(&db_path, None, OnNewIdentity::Trust)
        .await
        .context("Failed to open Signal database")?;

    // Check if already registered
    if Manager::load_registered(store.clone()).await.is_ok() {
        eprintln!("Already linked to Signal. Use 'signal-cli status' to check.");
        return Ok(());
    }

    eprintln!("Linking as secondary device...");
    eprintln!("Open Signal on your phone: Settings > Linked Devices > Link New Device");
    eprintln!();

    // Create channel for provisioning URL
    let (tx, rx) = oneshot::channel();

    // QR code file path
    let qr_file = get_data_dir()?.join("qr.png");
    let qr_file_cleanup = qr_file.clone();

    // Run linking and QR code display concurrently
    let (result, _) = future::join(
        Manager::link_secondary_device(
            store,
            SignalServers::Production,
            device_name.clone(),
            tx,
        ),
        async move {
            match rx.await {
                Ok(url) => {
                    let url_str = url.to_string();

                    // Save QR code to PNG file and open with system viewer
                    match qrcode::QrCode::new(&url_str) {
                        Ok(code) => {
                            let image = code.render::<image::Luma<u8>>().build();
                            if image.save(&qr_file).is_ok() {
                                eprintln!("QR code saved to: {}", qr_file.display());
                                // Open with system viewer (macOS: open, Linux: xdg-open)
                                #[cfg(target_os = "macos")]
                                let _ = ProcessCommand::new("open").arg(&qr_file).spawn();
                                #[cfg(target_os = "linux")]
                                let _ = ProcessCommand::new("xdg-open").arg(&qr_file).spawn();
                            }
                        }
                        Err(e) => {
                            warn!("Failed to generate QR code image: {}", e);
                        }
                    }

                    // Also print to terminal as fallback
                    eprintln!();
                    eprintln!("Scan this QR code with Signal:");
                    eprintln!("(Signal > Settings > Linked Devices > Link New Device)");
                    eprintln!();
                    qr2term::print_qr(&url_str).expect("Failed to render QR code");
                    eprintln!();
                    eprintln!("Or open this URL: {}", url_str);
                }
                Err(e) => {
                    eprintln!("Linking cancelled: {:?}", e);
                }
            }
        },
    )
    .await;

    // Clean up QR file on success
    let _ = std::fs::remove_file(&qr_file_cleanup);

    let manager = result?;
    let whoami = manager.whoami().await?;

    eprintln!("Successfully linked! Device: {}", device_name);

    let output = LinkOutput {
        success: true,
        uuid: whoami.aci.to_string(),
        device_name,
    };
    println!("{}", serde_json::to_string_pretty(&output)?);

    Ok(())
}

async fn cmd_whoami() -> Result<()> {
    let manager = load_registered_manager().await?;
    let whoami = manager.whoami().await?;

    let output = WhoamiOutput {
        uuid: whoami.aci.to_string(),
        phone: Some(whoami.number.to_string()),
        device_id: manager.device_id().into(),
    };

    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

async fn cmd_chats(max_results: usize) -> Result<()> {
    let manager = load_registered_manager().await?;
    let store = manager.store();

    let mut chats = Vec::new();

    // Add contacts
    for contact in store.contacts().await?.flatten() {
        chats.push(ChatOutput {
            id: contact.uuid.to_string(),
            name: contact.name.clone(),
            is_group: false,
            phone: contact.phone_number.map(|p| p.format().to_string()),
        });
    }

    // Add groups
    for (master_key, group) in store.groups().await?.flatten() {
        chats.push(ChatOutput {
            id: hex::encode(master_key),
            name: group.title.clone(),
            is_group: true,
            phone: None,
        });
    }

    // Limit results
    chats.truncate(max_results);

    println!("{}", serde_json::to_string_pretty(&chats)?);
    Ok(())
}

/// Resolve recipient to UUID - accepts UUID directly or contact name
async fn resolve_recipient(manager: &Manager<SqliteStore, Registered>, recipient: &str) -> Result<Uuid> {
    // Try parsing as UUID first
    if let Ok(uuid) = recipient.parse::<Uuid>() {
        return Ok(uuid);
    }

    // Search contacts by name (case-insensitive substring match)
    let search_lower = recipient.to_lowercase();
    let mut matches: Vec<_> = manager
        .store()
        .contacts()
        .await?
        .filter_map(|c| c.ok())
        .filter(|c| c.name.to_lowercase().contains(&search_lower))
        .collect();

    match matches.len() {
        0 => anyhow::bail!(
            "No contact found matching '{}'. Use a UUID or exact contact name.",
            recipient
        ),
        1 => Ok(matches.remove(0).uuid),
        _ => {
            let mut msg = format!(
                "Multiple contacts match '{}'. Use a UUID or more specific name:\n",
                recipient
            );
            for contact in &matches {
                let phone = contact
                    .phone_number
                    .as_ref()
                    .map(|p| p.format().to_string())
                    .unwrap_or_default();
                msg.push_str(&format!("  - {} ({}) {}\n", contact.name, contact.uuid, phone));
            }
            anyhow::bail!(msg)
        }
    }
}

async fn cmd_send(recipient: String) -> Result<()> {
    let mut manager = load_registered_manager().await?;

    // Resolve recipient (UUID or contact name)
    let uuid = resolve_recipient(&manager, &recipient).await?;

    // Read message from stdin
    let text = {
        use std::io::Read;
        let mut buf = String::new();
        std::io::stdin().read_to_string(&mut buf)?;
        buf.trim().to_string()
    };

    if text.is_empty() {
        anyhow::bail!("Message cannot be empty");
    }

    let timestamp = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)?
        .as_millis() as u64;

    // Build message
    let data_message = DataMessage {
        body: Some(text),
        timestamp: Some(timestamp),
        ..Default::default()
    };

    // Sync pending messages first
    let messages = manager
        .receive_messages()
        .await
        .context("failed to initialize messages stream")?;
    pin_mut!(messages);

    while let Some(content) = messages.next().await {
        match content {
            Received::QueueEmpty => break,
            Received::Contacts | Received::Content(_) => continue,
        }
    }

    // Send message
    manager
        .send_message(
            ServiceId::Aci(uuid.into()),
            ContentBody::DataMessage(data_message),
            timestamp,
        )
        .await?;

    let output = SendOutput {
        success: true,
        timestamp: (timestamp / 1000) as i64,
    };
    println!("{}", serde_json::to_string_pretty(&output)?);

    Ok(())
}

async fn cmd_receive() -> Result<()> {
    let mut manager = load_registered_manager().await?;

    eprintln!("Receiving messages...");

    // Open read sync database
    let mut read_db = read_sync::open_read_sync_db()?;

    let mut received_messages = Vec::new();
    let mut read_sync_count = 0;

    let messages = manager
        .receive_messages()
        .await
        .context("failed to initialize messages stream")?;
    pin_mut!(messages);

    while let Some(content) = messages.next().await {
        match content {
            Received::QueueEmpty => {
                eprintln!("Queue empty, done syncing");
                break;
            }
            Received::Contacts => {
                eprintln!("Received contacts sync");
            }
            Received::Content(c) => {
                match &c.body {
                    ContentBody::DataMessage(dm) => {
                        let ts = dm.timestamp.unwrap_or(0);
                        let sender_uuid = c.metadata.sender.raw_uuid();
                        let sender_aci = sender_uuid.to_string();

                        // Save message to store for later retrieval
                        let thread = Thread::Contact(sender_uuid);
                        if let Err(e) = manager.store().save_message(&thread, (*c).clone()).await {
                            warn!("Failed to save message: {}", e);
                        }

                        // Check if this message was already read (from a previous sync)
                        let is_read = read_sync::is_read(&read_db, &sender_aci, ts);

                        received_messages.push(MessageOutput {
                            id: ts.to_string(),
                            chat_id: sender_aci.clone(),
                            sender: sender_aci,
                            sender_name: None,
                            timestamp: (ts / 1000) as i64,
                            text: dm.body.clone().unwrap_or_default(),
                            is_outgoing: false,
                            is_read,
                        });
                    }
                    ContentBody::SynchronizeMessage(sm) => {
                        // Process read sync entries from other devices
                        if !sm.read.is_empty() {
                            match read_sync::process_sync_reads(&mut read_db, &sm.read) {
                                Ok(count) => {
                                    read_sync_count += count;
                                    debug!("Processed {} read sync entries", count);
                                }
                                Err(e) => {
                                    warn!("Failed to save read sync: {}", e);
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    if read_sync_count > 0 {
        eprintln!("Synced {} read receipts from other devices", read_sync_count);
    }
    eprintln!("Received {} messages", received_messages.len());
    println!("{}", serde_json::to_string_pretty(&received_messages)?);

    Ok(())
}

async fn cmd_messages(chat_id: String, max_results: usize) -> Result<()> {
    let manager = load_registered_manager().await?;
    let store = manager.store();
    let my_uuid = manager.whoami().await?.aci;

    // Open read sync database for is_read checks
    let read_db = read_sync::open_read_sync_db()?;

    // Parse chat_id as UUID (contact) or hex (group)
    let thread = if let Ok(uuid) = chat_id.parse::<Uuid>() {
        Thread::Contact(uuid)
    } else if let Ok(master_key) = hex::decode(&chat_id) {
        let key: [u8; 32] = master_key
            .try_into()
            .map_err(|_| anyhow::anyhow!("Group master key must be 32 bytes"))?;
        Thread::Group(key)
    } else {
        anyhow::bail!("Invalid chat_id: must be a UUID or 64-character hex string");
    };

    // Get messages from store (full range, newest first)
    let messages_iter = store.messages(&thread, ..).await?;
    let mut messages: Vec<MessageOutput> = Vec::new();

    for content in messages_iter.flatten().take(max_results) {
        if let ContentBody::DataMessage(dm) = &content.body {
            let ts = dm.timestamp.unwrap_or(0);
            let sender_uuid = content.metadata.sender.raw_uuid();
            let sender_aci = sender_uuid.to_string();
            let is_outgoing = sender_uuid == my_uuid;
            let is_read = read_sync::is_read(&read_db, &sender_aci, ts);

            messages.push(MessageOutput {
                id: ts.to_string(),
                chat_id: chat_id.clone(),
                sender: sender_aci,
                sender_name: None,
                timestamp: (ts / 1000) as i64,
                text: dm.body.clone().unwrap_or_default(),
                is_outgoing,
                is_read,
            });
        }
    }

    println!("{}", serde_json::to_string_pretty(&messages)?);
    Ok(())
}

async fn cmd_status() -> Result<()> {
    let store_result = open_store().await;

    let output = match store_result {
        Ok(store) => match Manager::load_registered(store).await {
            Ok(manager) => {
                let whoami = manager.whoami().await.ok();
                StatusOutput {
                    linked: true,
                    uuid: whoami.as_ref().map(|w| w.aci.to_string()),
                    phone: whoami.as_ref().map(|w| w.number.to_string()),
                }
            }
            Err(_) => StatusOutput {
                linked: false,
                uuid: None,
                phone: None,
            },
        },
        Err(_) => StatusOutput {
            linked: false,
            uuid: None,
            phone: None,
        },
    };

    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

async fn cmd_mark_read(chat_ids: Vec<String>) -> Result<()> {
    let manager = load_registered_manager().await?;
    let store = manager.store();
    let my_uuid = manager.whoami().await?.aci;

    let mut read_db = read_sync::open_read_sync_db()?;
    let mut total_messages = 0i64;
    let mut chats_marked = 0usize;

    for chat_id in &chat_ids {
        // Parse chat_id as UUID (contact) or hex (group)
        let thread = if let Ok(uuid) = chat_id.parse::<Uuid>() {
            Thread::Contact(uuid)
        } else if let Ok(master_key) = hex::decode(chat_id) {
            let key: [u8; 32] = master_key
                .try_into()
                .map_err(|_| anyhow::anyhow!("Group master key must be 32 bytes"))?;
            Thread::Group(key)
        } else {
            warn!("Invalid chat_id: {}", chat_id);
            continue;
        };

        // Get all incoming messages from this chat and mark them read
        // Collect (sender_aci, timestamp) pairs - groups have multiple senders
        let messages_iter = store.messages(&thread, ..).await?;
        let mut to_mark: Vec<(String, u64)> = Vec::new();

        for content in messages_iter.flatten() {
            if let ContentBody::DataMessage(dm) = &content.body {
                let sender_uuid = content.metadata.sender.raw_uuid();
                if sender_uuid != my_uuid {
                    // Incoming message
                    if let Some(ts) = dm.timestamp {
                        let sender_aci = sender_uuid.to_string();
                        if !read_sync::is_read(&read_db, &sender_aci, ts) {
                            to_mark.push((sender_aci, ts));
                        }
                    }
                }
            }
        }

        // Mark each message with its actual sender
        for (sender_aci, ts) in &to_mark {
            read_sync::mark_sender_read(&mut read_db, sender_aci, &[*ts])?;
        }
        total_messages += to_mark.len() as i64;
        chats_marked += 1;
    }

    let output = MarkReadOutput {
        success: true,
        chats_marked,
        messages_marked: total_messages,
    };
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    // Initialize logging
    if cli.verbose {
        tracing_subscriber::fmt()
            .with_env_filter("debug")
            .with_writer(std::io::stderr)
            .init();
    }

    match cli.command {
        Command::Link { device_name } => cmd_link(device_name).await,
        Command::Whoami => cmd_whoami().await,
        Command::Chats { max_results } => cmd_chats(max_results).await,
        Command::Send { recipient } => cmd_send(recipient).await,
        Command::Receive => cmd_receive().await,
        Command::Messages { chat_id, max_results } => cmd_messages(chat_id, max_results).await,
        Command::Status => cmd_status().await,
        Command::MarkRead { chat_ids } => cmd_mark_read(chat_ids).await,
    }
}
