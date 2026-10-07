use super::*;

impl EventStore {
    /// Includes writes through this connection and changes made by another connection.
    pub(crate) fn learning_timeline_version(&self) -> Result<String, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let local: i64 = connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        let external: i64 = connection
            .query_row("PRAGMA data_version", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        Ok(format!("{local}:{external}"))
    }

    /// Read complete stored values for the learning ledger without conversation preview caps.
    /// Metadata's original text is retained even when it is not valid JSON.
    pub(crate) fn learning_timeline_page(
        &self,
        after_id: i64,
        limit: usize,
    ) -> Result<Vec<Value>, String> {
        if limit == 0 || limit > 1000 {
            return Err("Learning timeline page size must be between 1 and 1000".into());
        }
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT t.id,t.conversation_id,t.timestamp,t.kind,t.role,t.source,t.title,t.content,t.metadata,
             COALESCE(b.workspace_origin,t.conversation_id),c.project_id,c.client,c.profile
             FROM timeline t LEFT JOIN conversation_branches b ON b.id=t.conversation_id
             LEFT JOIN conversations c ON c.id=t.conversation_id
             WHERE t.id>?1 ORDER BY t.id LIMIT ?2"
        ).map_err(|error| error.to_string())?;
        let rows = statement.query_map(params![after_id,limit as i64], |row| {
            let metadata_text: String = row.get(8)?;
            Ok(json!({
                "id":row.get::<_,i64>(0)?,"conversationId":row.get::<_,String>(1)?,
                "timestamp":row.get::<_,String>(2)?,"kind":row.get::<_,String>(3)?,
                "role":row.get::<_,String>(4)?,"source":row.get::<_,String>(5)?,
                "title":row.get::<_,String>(6)?,"content":row.get::<_,String>(7)?,
                "metadata":serde_json::from_str::<Value>(&metadata_text).unwrap_or_else(|error|json!({"integrityError":format!("Timeline metadata JSON parse failed: {error}")})),
                "metadataText":metadata_text,"sourceConversationId":row.get::<_,String>(9)?,
                "projectId":row.get::<_,Option<String>>(10)?,"client":row.get::<_,Option<String>>(11)?,
                "profile":row.get::<_,Option<String>>(12)?
            }))
        }).map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())
    }
}
