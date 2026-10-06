use super::*;
use std::io::Write;

impl EventStore {
    /// Stream a coherent, complete history without the sidebar or preview limits.
    pub fn write_conversation_export(
        &self,
        id: &str,
        format: &str,
        output: &mut dyn Write,
    ) -> Result<(), String> {
        if !matches!(format, "json" | "markdown") {
            return Err("Export format must be json or markdown".into());
        }
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| error.to_string())?;
        let conversation = transaction.query_row(
            "SELECT c.id,c.title,c.client,c.created_at,c.updated_at,c.profile,c.status,
             (SELECT COUNT(*) FROM timeline t WHERE t.conversation_id=c.id AND t.kind='message'),c.project,c.project_id,c.pinned
             FROM conversations c WHERE c.id=?1", [id], |row| Ok(ConversationSummary {
                id:row.get(0)?, title:row.get(1)?, client:row.get(2)?, created_at:row.get(3)?, updated_at:row.get(4)?,
                profile:row.get(5)?, status:row.get(6)?, message_count:row.get::<_,i64>(7)? as u64,
                project:row.get(8)?, project_id:row.get(9)?, pinned:row.get::<_,i64>(10)? != 0,
            })
        ).optional().map_err(|error| error.to_string())?.ok_or("Conversation not found")?;
        if format == "json" {
            output
                .write_all(b"{\"format\":\"opencore-chat\",\"version\":1,\"conversation\":")
                .map_err(|error| error.to_string())?;
            serde_json::to_writer(&mut *output, &conversation)
                .map_err(|error| error.to_string())?;
            output
                .write_all(b",\"conversation_id\":")
                .map_err(|error| error.to_string())?;
            serde_json::to_writer(&mut *output, id).map_err(|error| error.to_string())?;
            output
                .write_all(b",\"entries\":[")
                .map_err(|error| error.to_string())?;
        } else {
            writeln!(
                output,
                "# {}\n\nOpenCore conversation {}\n",
                conversation.title, id
            )
            .map_err(|error| error.to_string())?;
        }
        let mut statement = transaction.prepare(
            "SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata FROM timeline WHERE conversation_id=?1 ORDER BY timestamp,id"
        ).map_err(|error| error.to_string())?;
        let rows = statement
            .query_map([id], |row| {
                let metadata: String = row.get(8)?;
                Ok(TimelineEntry {
                    id: row.get(0)?,
                    conversation_id: row.get(1)?,
                    timestamp: row.get(2)?,
                    kind: row.get(3)?,
                    role: row.get(4)?,
                    source: row.get(5)?,
                    title: row.get(6)?,
                    content: row.get(7)?,
                    metadata: serde_json::from_str(&metadata).unwrap_or_else(|_| json!({})),
                })
            })
            .map_err(|error| error.to_string())?;
        let mut first = true;
        for row in rows {
            let entry = row.map_err(|error| error.to_string())?;
            if format == "json" {
                if !first {
                    output.write_all(b",").map_err(|error| error.to_string())?;
                }
                serde_json::to_writer(&mut *output, &entry).map_err(|error| error.to_string())?;
                first = false;
            } else {
                writeln!(
                    output,
                    "## {} · {} · {}\n\n{}\n",
                    entry.timestamp, entry.source, entry.title, entry.content
                )
                .map_err(|error| error.to_string())?;
            }
        }
        if format == "json" {
            output
                .write_all(b"]}\n")
                .map_err(|error| error.to_string())?;
        }
        output.flush().map_err(|error| error.to_string())?;
        drop(statement);
        transaction.commit().map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn export_contains_full_history_and_metadata_beyond_the_preview_limits() {
        let directory =
            std::env::temp_dir().join(format!("opencore-export-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let store = EventStore::open(&directory.join("history.sqlite3")).unwrap();
        store
            .ensure_conversation(
                "local:export",
                "OpenCore",
                "echo",
                "Long saved conversation",
            )
            .unwrap();
        {
            let mut connection = store.connection.lock().unwrap();
            let transaction = connection.transaction().unwrap();
            for index in 0..601 {
                transaction.execute("INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata) VALUES(?1,?2,'message','assistant','OpenCore','Reply',?3,?4)",
                    params!["local:export", (Utc::now() + chrono::Duration::seconds(index)).to_rfc3339(), format!("event-{index}"), json!({"exact":index}).to_string()]).unwrap();
            }
            transaction.commit().unwrap();
        }
        assert_eq!(store.conversation("local:export").unwrap().len(), 500);
        let mut json_output = Vec::new();
        store
            .write_conversation_export("local:export", "json", &mut json_output)
            .unwrap();
        let export: Value = serde_json::from_slice(&json_output).unwrap();
        assert_eq!(export["format"], "opencore-chat");
        assert_eq!(export["conversation"]["title"], "Long saved conversation");
        assert_eq!(export["entries"].as_array().unwrap().len(), 601);
        assert_eq!(export["entries"][0]["content"], "event-0");
        assert_eq!(export["entries"][600]["metadata"]["exact"], 600);
        let source = directory.join("portable.json");
        std::fs::write(&source, &json_output).unwrap();
        let copied = crate::chat_import::import_file(&store, &source, "auto").unwrap();
        assert_eq!(copied.imported, 1);
        assert_eq!(copied.conversations[0].entries, 601);
        assert_eq!(
            store
                .conversation_messages("local:export")
                .unwrap()
                .last()
                .unwrap()
                .content,
            "event-600"
        );
        assert_eq!(std::fs::read(source).unwrap(), json_output);
        let mut markdown_output = Vec::new();
        store
            .write_conversation_export("local:export", "markdown", &mut markdown_output)
            .unwrap();
        let markdown = String::from_utf8(markdown_output).unwrap();
        assert!(markdown.contains("event-0\n"));
        assert!(markdown.contains("event-600\n"));
        assert!(store
            .write_conversation_export("missing", "json", &mut Vec::new())
            .is_err());
        assert!(store
            .write_conversation_export("local:export", "unsupported", &mut Vec::new())
            .is_err());
        drop(store);
        std::fs::remove_dir_all(directory).unwrap();
    }
}
