//! Imported chat pages are independent of the bounded main sidebar snapshot.
use super::*;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportedConversationPage {
    pub conversations: Vec<ConversationSummary>,
    pub total: u64,
    pub offset: usize,
    pub limit: usize,
}

const SUMMARY_COLUMNS: &str = "c.id,c.title,c.client,c.created_at,c.updated_at,c.profile,c.status,
    (SELECT COUNT(*) FROM timeline t WHERE t.conversation_id=c.id AND t.kind='message'),
    c.project,c.project_id,c.pinned";

// instr treats %, _ and backslash as literal text, unlike LIKE. Keep this
// predicate shared by the count and page so their search scope cannot diverge.
const IMPORTED_SEARCH: &str = "c.client GLOB 'Imported *' AND (
    instr(lower(c.title),lower(?1)) > 0 OR instr(lower(c.client),lower(?1)) > 0
    OR instr(lower(c.id),lower(?1)) > 0 OR instr(lower(c.project),lower(?1)) > 0)";

const IMPORTED_SOURCE: &str = "(?2='all'
    OR (?2='hermes' AND c.client='Imported Hermes')
    OR (?2='opencode' AND c.client='Imported OpenCode')
    OR (?2='codex' AND c.client='Imported Codex')
    OR (?2='claude' AND c.client='Imported Claude Code')
    OR (?2='other' AND c.client NOT IN
      ('Imported Hermes','Imported OpenCode','Imported Codex','Imported Claude Code')))";

fn imported_summary_from_row(row: &Row<'_>) -> rusqlite::Result<ConversationSummary> {
    Ok(ConversationSummary {
        id: row.get(0)?,
        title: row.get(1)?,
        client: row.get(2)?,
        created_at: row.get(3)?,
        updated_at: row.get(4)?,
        profile: row.get(5)?,
        status: row.get(6)?,
        message_count: row.get::<_, i64>(7)? as u64,
        project: row.get(8)?,
        project_id: row.get(9)?,
        pinned: row.get::<_, i64>(10)? != 0,
    })
}

impl EventStore {
    /// List complete imported history without the main snapshot's 500-row cap.
    pub fn list_imported_conversations(
        &self,
        query: &str,
        offset: usize,
        limit: usize,
    ) -> Result<ImportedConversationPage, String> {
        self.list_imported_conversations_for_source(query, offset, limit, "all")
    }

    /// Filter the complete imported library before counting, ordering and paging.
    pub fn list_imported_conversations_for_source(
        &self,
        query: &str,
        offset: usize,
        limit: usize,
        source: &str,
    ) -> Result<ImportedConversationPage, String> {
        if !matches!(
            source,
            "all" | "hermes" | "opencode" | "codex" | "claude" | "other"
        ) {
            return Err("Choose a supported imported chat source.".into());
        }
        let sql_offset =
            i64::try_from(offset).map_err(|_| "Imported chat offset is too large".to_string())?;
        let limit = limit.clamp(1, 100);
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| error.to_string())?;
        // A read transaction gives the count and page the same WAL snapshot,
        // even when another connection imports or deletes chats between them.
        let total = transaction
            .query_row(
                &format!("SELECT COUNT(*) FROM conversations c WHERE {IMPORTED_SEARCH} AND {IMPORTED_SOURCE}"),
                params![query, source],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| error.to_string())? as u64;
        let conversations = {
            let mut statement = transaction
                .prepare(&format!(
                    "SELECT {SUMMARY_COLUMNS} FROM conversations c WHERE {IMPORTED_SEARCH} AND {IMPORTED_SOURCE}
                 ORDER BY c.pinned DESC,c.updated_at DESC,c.id ASC LIMIT ?3 OFFSET ?4"
                ))
                .map_err(|error| error.to_string())?;
            let rows = statement
                .query_map(
                    params![query, source, limit as i64, sql_offset],
                    imported_summary_from_row,
                )
                .map_err(|error| error.to_string())?;
            rows.collect::<Result<Vec<_>, _>>()
                .map_err(|error| error.to_string())?
        };
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(ImportedConversationPage {
            conversations,
            total,
            offset,
            limit,
        })
    }

    /// Resolve a selected imported ID even if it is absent from cached pages.
    pub fn imported_conversation_summary(
        &self,
        id: &str,
    ) -> Result<Option<ConversationSummary>, String> {
        self.connection
            .lock()
            .map_err(|error| error.to_string())?
            .query_row(
                &format!(
                    "SELECT {SUMMARY_COLUMNS} FROM conversations c
                      WHERE c.id=?1 AND c.client GLOB 'Imported *'"
                ),
                [id],
                imported_summary_from_row,
            )
            .optional()
            .map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use std::path::PathBuf;

    struct Fixture {
        root: PathBuf,
        store: Option<EventStore>,
    }

    impl Fixture {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("opencore-import-list-{}", uuid::Uuid::new_v4()));
            let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
            Self {
                root,
                store: Some(store),
            }
        }

        fn store(&self) -> &EventStore {
            self.store.as_ref().unwrap()
        }

        fn populate(&self, operation: impl FnOnce(&rusqlite::Transaction<'_>)) {
            let mut connection = self.store().connection.lock().unwrap();
            let transaction = connection.transaction().unwrap();
            operation(&transaction);
            transaction.commit().unwrap();
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            drop(self.store.take());
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn insert(
        transaction: &rusqlite::Transaction<'_>,
        id: &str,
        title: &str,
        client: &str,
        project: &str,
        updated_at: &str,
        pinned: bool,
    ) {
        transaction.execute("INSERT INTO conversations(id,title,client,profile,status,created_at,updated_at,project,pinned)
            VALUES(?1,?2,?3,'history','imported',?4,?4,?5,?6)",
            params![id,title,client,updated_at,project,pinned]).unwrap();
    }

    #[test]
    fn old_imports_remain_visible_and_selectable_beyond_the_main_snapshot_cap() {
        let fixture = Fixture::new();
        fixture.populate(|transaction| {
            for index in 0..501 {
                insert(transaction, &format!("native-{index:04}"), "New native chat", "OpenCore", "", "2026-10-06", false);
            }
            insert(transaction, "imported:old", "Old imported chat", "Imported Hermes", "Old workspace", "2000-01-01", false);
            transaction.execute("INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata)
                VALUES('imported:old','2000-01-01','message','user','Imported Hermes','User','First','{}'),
                ('imported:old','2000-01-02','message','assistant','Imported Hermes','Assistant','Second','{}'),
                ('imported:old','2000-01-02','thinking','assistant','Imported Hermes','Reasoning','Third','{}')", []).unwrap();
        });
        let main = fixture.store().list_conversations(None).unwrap();
        assert_eq!(main.len(), 500);
        assert!(!main.iter().any(|chat| chat.id == "imported:old"));
        let page = fixture
            .store()
            .list_imported_conversations("", 0, 50)
            .unwrap();
        assert_eq!((page.total, page.offset, page.limit), (1, 0, 50));
        assert_eq!(page.conversations.len(), 1);
        assert_eq!(page.conversations[0].id, "imported:old");
        assert_eq!(page.conversations[0].updated_at, "2000-01-01");
        assert_eq!(page.conversations[0].message_count, 2);
        let exact = fixture
            .store()
            .imported_conversation_summary("imported:old")
            .unwrap()
            .unwrap();
        assert_eq!(exact.title, "Old imported chat");
        assert_eq!(exact.project, "Old workspace");
        assert_eq!(exact.message_count, 2);
        assert!(fixture
            .store()
            .imported_conversation_summary("native-0000")
            .unwrap()
            .is_none());
        assert!(fixture
            .store()
            .imported_conversation_summary("missing")
            .unwrap()
            .is_none());
    }

    #[test]
    fn a_thousand_imports_have_complete_stable_pages_and_literal_search() {
        let fixture = Fixture::new();
        fixture.populate(|transaction| {
            for index in 0..1_000 {
                insert(
                    transaction,
                    &format!("imported-{index:04}"),
                    &format!("Imported thread {index}"),
                    if index % 2 == 0 {
                        "Imported JSON"
                    } else {
                        "Imported Hermes"
                    },
                    "",
                    if index == 998 {
                        "2022-01-01"
                    } else {
                        "2020-01-01"
                    },
                    index == 999,
                );
            }
            for (index, client) in [
                "OpenCore",
                "Reimported Hermes",
                "imported Hermes",
                "ImportedX Hermes",
                "Imported",
            ]
            .into_iter()
            .enumerate()
            {
                insert(
                    transaction,
                    &format!("not-imported-{index}"),
                    "Imported thread native",
                    client,
                    "",
                    "2030-01-01",
                    true,
                );
            }
            transaction
                .execute(
                    r"UPDATE conversations SET title='Literal %_\ marker' WHERE id='imported-0321'",
                    [],
                )
                .unwrap();
            transaction
                .execute(
                    "UPDATE conversations SET title='Literal xxq marker' WHERE id='imported-0322'",
                    [],
                )
                .unwrap();
            transaction
                .execute(
                    "UPDATE conversations SET project='Landing Alpha' WHERE id='imported-0420'",
                    [],
                )
                .unwrap();
        });
        let mut ids = Vec::new();
        for offset in (0..1_000).step_by(100) {
            let page = fixture
                .store()
                .list_imported_conversations("", offset, 100)
                .unwrap();
            assert_eq!((page.total, page.offset, page.limit), (1_000, offset, 100));
            assert_eq!(page.conversations.len(), 100);
            ids.extend(page.conversations.into_iter().map(|chat| chat.id));
        }
        assert_eq!(ids.len(), 1_000);
        assert_eq!(ids.iter().collect::<HashSet<_>>().len(), 1_000);
        assert_eq!(
            ids[0], "imported-0999",
            "pinned ordering must outrank a newer update"
        );
        assert_eq!(
            ids[1], "imported-0998",
            "newer unpinned updates must precede older updates"
        );
        assert_eq!(
            ids[2], "imported-0000",
            "equal updates must use stable ID ordering"
        );
        assert_eq!(ids.last().unwrap(), "imported-0997");

        for (query, total, expected_id) in [
            ("IMPORTed THREAD 999", 1, Some("imported-0999")),
            ("imported-0998", 1, Some("imported-0998")),
            ("ALPHA", 1, Some("imported-0420")),
            ("%_\\", 1, Some("imported-0321")),
            ("HERMES", 500, None),
            ("no matching imported chat", 0, None),
        ] {
            let page = fixture
                .store()
                .list_imported_conversations(query, 0, 100)
                .unwrap();
            assert_eq!(page.total, total, "query {query:?}");
            if let Some(expected_id) = expected_id {
                assert_eq!(page.conversations[0].id, expected_id, "query {query:?}");
            }
            assert!(page
                .conversations
                .iter()
                .all(|chat| chat.client.starts_with("Imported ")));
        }
        let outside = fixture
            .store()
            .list_imported_conversations("", 1_000, 100)
            .unwrap();
        assert_eq!(outside.total, 1_000);
        assert!(outside.conversations.is_empty());
    }

    #[test]
    fn source_categories_have_complete_disjoint_pages_and_keep_project_and_pin_fields() {
        let fixture = Fixture::new();
        fixture.populate(|transaction| {
            transaction.execute("INSERT INTO projects(id,name,folder_path,created_at,updated_at) VALUES('source-project','Source folder',?1,'2020','2020')",[fixture.root.to_string_lossy().as_ref()]).unwrap();
            for index in 0..600 {
                insert(transaction,&format!("native-{index:04}"),"Newer native chat","OpenCore","","2040-01-01",false);
            }
            for (source,client) in [("hermes","Imported Hermes"),("opencode","Imported OpenCode"),("codex","Imported Codex"),("claude","Imported Claude Code")] {
                for index in 0..205 {
                    insert(transaction,&format!("{source}-{index:04}"),&format!("{source} notes {index}"),client,"Source folder","2000-01-01",source=="hermes" && index==204);
                }
            }
            for (id,client) in [("json","Imported JSON"),("opencore","Imported OpenCore"),("unknown","Imported Future Client"),("lookalike","Imported Hermes Extra")] {
                insert(transaction,id,id,client,"","2000-01-01",false);
            }
            transaction.execute("UPDATE conversations SET project_id='source-project' WHERE id='hermes-0204'",[]).unwrap();
        });
        let mut categorized = HashSet::new();
        for (source, client, total) in [
            ("hermes", "Imported Hermes", 205),
            ("opencode", "Imported OpenCode", 205),
            ("codex", "Imported Codex", 205),
            ("claude", "Imported Claude Code", 205),
            ("other", "", 4),
        ] {
            let mut ids = Vec::new();
            for offset in (0..total).step_by(100) {
                let page = fixture
                    .store()
                    .list_imported_conversations_for_source("", offset, 100, source)
                    .unwrap();
                assert_eq!(
                    (page.total, page.offset, page.limit),
                    (total as u64, offset, 100),
                    "{source}"
                );
                assert!(page
                    .conversations
                    .iter()
                    .all(|chat| client.is_empty() || chat.client == client));
                if source == "hermes" && offset == 0 {
                    assert_eq!(page.conversations[0].id, "hermes-0204");
                    assert!(page.conversations[0].pinned);
                    assert_eq!(page.conversations[0].project, "Source folder");
                    assert_eq!(
                        page.conversations[0].project_id.as_deref(),
                        Some("source-project")
                    );
                }
                ids.extend(page.conversations.into_iter().map(|chat| chat.id));
            }
            assert_eq!(ids.len(), total);
            for id in ids {
                assert!(
                    categorized.insert(id),
                    "A chat appeared under two source categories"
                );
            }
        }
        let mut all = HashSet::new();
        for offset in (0..824).step_by(100) {
            let page = fixture
                .store()
                .list_imported_conversations_for_source("", offset, 100, "all")
                .unwrap();
            assert_eq!(page.total, 824);
            all.extend(page.conversations.into_iter().map(|chat| chat.id));
        }
        assert_eq!(categorized, all);
        assert_eq!(
            fixture
                .store()
                .list_imported_conversations("", 0, 100)
                .unwrap()
                .total,
            824
        );
    }

    #[test]
    fn source_filter_and_literal_search_apply_to_count_and_later_pages_together() {
        let fixture = Fixture::new();
        fixture.populate(|transaction| {
            for (source, client) in [
                ("hermes", "Imported Hermes"),
                ("opencode", "Imported OpenCode"),
            ] {
                for index in 0..150 {
                    insert(
                        transaction,
                        &format!("{source}-{index:04}"),
                        "Shared search",
                        client,
                        "Original project",
                        "2000",
                        false,
                    );
                }
                transaction
                    .execute(
                        "UPDATE conversations SET title=?1 WHERE id=?2",
                        params![r"Literal %_\ marker", format!("{source}-0149")],
                    )
                    .unwrap();
            }
        });
        for source in ["hermes", "opencode"] {
            let later = fixture
                .store()
                .list_imported_conversations_for_source("SHARED", 100, 100, source)
                .unwrap();
            assert_eq!((later.total, later.conversations.len()), (149, 49));
            assert!(later
                .conversations
                .iter()
                .all(|chat| chat.id.starts_with(source)));
            let literal = fixture
                .store()
                .list_imported_conversations_for_source(r"%_\", 0, 100, source)
                .unwrap();
            assert_eq!(literal.total, 1);
            assert_eq!(literal.conversations[0].id, format!("{source}-0149"));
            let project = fixture
                .store()
                .list_imported_conversations_for_source("ORIGINAL PROJECT", 100, 100, source)
                .unwrap();
            assert_eq!((project.total, project.conversations.len()), (150, 50));
        }
        assert_eq!(
            fixture
                .store()
                .list_imported_conversations_for_source("hermes", 0, 100, "opencode")
                .unwrap()
                .total,
            0
        );
        for invalid in [
            "Hermes",
            "hermes ",
            "Imported Hermes",
            "",
            "hermes' OR 1=1 --",
        ] {
            assert!(fixture
                .store()
                .list_imported_conversations_for_source("", 0, 100, invalid)
                .unwrap_err()
                .contains("source"));
        }
    }

    #[test]
    fn pagination_clamps_limits_preserves_empty_page_totals_and_rejects_offset_overflow() {
        let fixture = Fixture::new();
        fixture.populate(|transaction| {
            for id in ["a", "b", "c"] {
                insert(
                    transaction,
                    id,
                    id,
                    "Imported JSON",
                    "",
                    "2020-01-01",
                    false,
                );
            }
        });
        let minimum = fixture
            .store()
            .list_imported_conversations("", 0, 0)
            .unwrap();
        assert_eq!(
            (minimum.total, minimum.limit, minimum.conversations.len()),
            (3, 1, 1)
        );
        let maximum = fixture
            .store()
            .list_imported_conversations("", 0, usize::MAX)
            .unwrap();
        assert_eq!(
            (maximum.total, maximum.limit, maximum.conversations.len()),
            (3, 100, 3)
        );
        let empty = fixture
            .store()
            .list_imported_conversations("", 999, 500)
            .unwrap();
        assert_eq!((empty.total, empty.offset, empty.limit), (3, 999, 100));
        assert!(empty.conversations.is_empty());
        #[cfg(target_pointer_width = "64")]
        assert!(fixture
            .store()
            .list_imported_conversations("", usize::MAX, 100)
            .is_err());
    }
}
