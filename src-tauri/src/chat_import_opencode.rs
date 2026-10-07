//! OpenCode's official {info,messages:[{info,parts}]} export and both SQLite message families.
//! Everything is copied as inert history, including tools, shell commands and source permissions.
use super::*;

pub(super) fn prepare_documents(
    records: Vec<(usize, Result<Value, String>)>,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    let mut conversations = vec![];
    let (mut entries, mut bytes) = (0, 0);
    for (line, value) in records {
        check_cancelled(cancelled)?;
        match value {
            Ok(Value::Array(exports)) => {
                for export in exports {
                    append_candidate(
                        &mut conversations,
                        &mut entries,
                        &mut bytes,
                        parse_export(export, cancelled)?,
                    )?;
                }
            }
            Ok(export) => append_candidate(
                &mut conversations,
                &mut entries,
                &mut bytes,
                parse_export(export, cancelled)?,
            )?,
            Err(error) => append_candidate(
                &mut conversations,
                &mut entries,
                &mut bytes,
                failed_candidate(
                    "opencode",
                    &format!("line:{line}"),
                    "Unreadable OpenCode conversation",
                    error,
                ),
            )?,
        }
    }
    Ok(PreparedFile {
        source_format: "opencode".into(),
        conversations,
    })
}

fn export_header(value: &Value) -> Result<Value, String> {
    let mut header = value
        .get("info")
        .filter(|value| value.is_object())
        .cloned()
        .ok_or("An OpenCode export must contain a session info object")?;
    // Keep project registry and any export-level metadata alongside session metadata.
    let mut envelope = value.as_object().cloned().unwrap_or_default();
    envelope.remove("info");
    envelope.remove("messages");
    if !envelope.is_empty() {
        header["sourceExportMetadata"] = Value::Object(envelope);
    }
    if let Some(project) = value.get("project").or_else(|| value.get("sourceProject")) {
        projects::attach_opencode_project(&mut header, project);
    }
    Ok(header)
}

fn parse_export(value: Value, cancelled: &dyn Fn() -> bool) -> Result<Candidate, String> {
    check_cancelled(cancelled)?;
    let id = scalar_id(value.pointer("/info/id"))
        .unwrap_or_else(|| format!("anonymous:{}", digest(value.to_string().as_bytes())));
    let header = match export_header(&value) {
        Ok(header) => header,
        Err(error) => {
            return Ok(failed_candidate(
                "opencode",
                &id,
                "OpenCode conversation",
                error.into(),
            ))
        }
    };
    let title = header
        .get("title")
        .and_then(Value::as_str)
        .map(short_title)
        .unwrap_or_default();
    let mut builder = Builder::new("opencode", &id, &title, header.clone());
    let result = (|| {
        if id.len() > 1024 {
            return Err("The source conversation ID is too long.".into());
        }
        builder.header = safe_value(&header, 0)?;
        if value.pointer("/info/id").is_none() {
            builder
                .warn("This export has no conversation ID. Changed exports create another copy.");
        }
        let messages = value
            .get("messages")
            .and_then(Value::as_array)
            .ok_or("An OpenCode export must contain a messages array")?;
        if messages.len() > MAX_ENTRIES {
            return Err("The conversation exceeds the 50,000 message limit.".into());
        }
        for (index, raw) in messages.iter().enumerate() {
            check_cancelled(cancelled)?;
            let safe = safe_value(raw, 0)?;
            parse_message(&mut builder, &safe, cancelled).map_err(|error| {
                if error == IMPORT_CANCELLED {
                    error
                } else {
                    format!("OpenCode message {}: {error}", index + 1)
                }
            })?;
        }
        Ok::<(), String>(())
    })();
    match result {
        Err(error) if error == IMPORT_CANCELLED => Err(error),
        Err(error) => Ok(builder.failed(error)),
        Ok(()) => Ok(builder.finish()),
    }
}

fn recorded_time<'a>(info: &'a Value) -> Option<&'a Value> {
    info.pointer("/time/created")
        .or_else(|| info.get("time_created"))
        .or_else(|| info.get("timestamp"))
        .or_else(|| info.get("created_at"))
}

fn push_part(
    builder: &mut Builder,
    info: &Value,
    raw: &Value,
    identity: &str,
    timestamp: Option<&Value>,
    part: Part,
) -> Result<(), String> {
    let stamp = builder.timestamp(timestamp)?;
    builder.push((
        stamp,
        part.kind,
        part.role,
        part.title,
        part.content,
        json!({"sourceRecord":raw,"sourceMessage":info,
            "portableImport":builder.provenance(identity,timestamp,Some("OpenCode"))}),
    ))
}

fn message_info(raw: &Value) -> &Value {
    raw.get("info").unwrap_or(raw)
}

fn parse_message(
    builder: &mut Builder,
    raw: &Value,
    cancelled: &dyn Fn() -> bool,
) -> Result<(), String> {
    let info = raw.get("info").unwrap_or(raw);
    let id = scalar_id(info.get("id")).ok_or("The source message has no ID")?;
    if info
        .get("sessionID")
        .or_else(|| info.get("session_id"))
        .and_then(Value::as_str)
        .is_some_and(|session| session != builder.source_id)
    {
        return Err("The source message belongs to another session".into());
    }
    let identity_record = json!({"id":id,"record":raw});
    let Some(identity) = builder.record_identity(&identity_record, "opencode-message")? else {
        return Ok(());
    };
    let typ = info
        .get("role")
        .or_else(|| info.get("type"))
        .and_then(Value::as_str)
        .ok_or("The source message has no role/type")?;
    let timestamp = recorded_time(info);
    if matches!(
        typ,
        "user" | "assistant" | "system" | "developer" | "synthetic"
    ) {
        let role = if typ == "synthetic" { "system" } else { typ };
        if let Some(parts) = raw
            .get("parts")
            .or_else(|| info.get("content"))
            .and_then(Value::as_array)
        {
            if parts.len() > MAX_ENTRIES {
                return Err("The source message exceeds the part limit".into());
            }
            if parts.is_empty() {
                builder.warn(
                    "An empty OpenCode message was omitted; its session metadata is preserved.",
                );
            }
            let mut seen_parts = HashMap::new();
            for (index, part) in parts.iter().enumerate() {
                check_cancelled(cancelled)?;
                let part_id = scalar_id(part.get("id")).unwrap_or_else(|| index.to_string());
                let fingerprint = digest(part.to_string().as_bytes());
                if let Some(previous) = seen_parts.insert(part_id.clone(), fingerprint.clone()) {
                    if previous != fingerprint {
                        return Err("Conflicting OpenCode parts have the same ID".into());
                    }
                    builder.warn("A repeated source part was ignored.");
                    continue;
                }
                if part
                    .get("messageID")
                    .or_else(|| part.get("message_id"))
                    .and_then(Value::as_str)
                    .is_some_and(|message| message != id)
                {
                    return Err("A source part belongs to another message".into());
                }
                parse_part(
                    builder,
                    info,
                    part,
                    role,
                    &format!("{identity}:part:{part_id}"),
                    timestamp,
                )?;
            }
            if let Some(error) = info.get("error").filter(|value| !value.is_null()) {
                push_part(
                    builder,
                    info,
                    info,
                    &format!("{identity}:error"),
                    timestamp,
                    part("activity", "system", "OpenCode error", text_value(error)),
                )?;
            }
            return Ok(());
        }
        if let Some(text) = info
            .get("text")
            .or_else(|| info.get("content"))
            .and_then(Value::as_str)
        {
            if !text.is_empty() {
                return push_part(
                    builder,
                    info,
                    info,
                    &format!("{identity}:text"),
                    timestamp,
                    part("message", role, role, text.into()),
                );
            }
        }
        if info
            .get("files")
            .and_then(Value::as_array)
            .is_some_and(|files| !files.is_empty())
        {
            return push_part(
                builder,
                info,
                info,
                &format!("{identity}:files"),
                timestamp,
                part("activity", role, "Attachments", text_value(&info["files"])),
            );
        }
        builder.warn("An empty OpenCode message was omitted.");
        return Ok(());
    }
    if typ == "shell" {
        push_part(
            builder,
            info,
            info,
            &format!("{identity}:call"),
            timestamp,
            part(
                "tool_call",
                "assistant",
                "Shell",
                text_value(info.get("command").unwrap_or(&Value::Null)),
            ),
        )?;
        return push_part(
            builder,
            info,
            info,
            &format!("{identity}:result"),
            info.pointer("/time/completed").or(timestamp),
            part(
                "tool_result",
                "tool",
                "Shell result",
                text_value(info.get("output").unwrap_or(&Value::Null)),
            ),
        );
    }
    push_part(
        builder,
        info,
        info,
        &format!("{identity}:activity"),
        timestamp,
        part("activity", "system", typ, text_value(info)),
    )
}

fn parse_part(
    builder: &mut Builder,
    info: &Value,
    raw: &Value,
    role: &str,
    identity: &str,
    timestamp: Option<&Value>,
) -> Result<(), String> {
    let timestamp = raw
        .pointer("/time/start")
        .or_else(|| recorded_time(raw))
        .or(timestamp);
    let typ = raw
        .get("type")
        .and_then(Value::as_str)
        .ok_or("A source part has no type")?;
    match typ {
        "text" | "reasoning" => {
            let text = raw
                .get("text")
                .and_then(Value::as_str)
                .ok_or("A source text part has no text")?;
            push_part(
                builder,
                info,
                raw,
                identity,
                timestamp,
                part(
                    if typ == "reasoning" {
                        "thinking"
                    } else {
                        "message"
                    },
                    role,
                    if typ == "reasoning" {
                        "Reasoning"
                    } else {
                        role
                    },
                    text.into(),
                ),
            )
        }
        "tool" => {
            let name = raw
                .get("tool")
                .or_else(|| raw.get("name"))
                .and_then(Value::as_str)
                .ok_or("A source tool has no name")?;
            let state = raw
                .get("state")
                .filter(|state| state.is_object())
                .ok_or("A source tool has no state")?;
            push_part(
                builder,
                info,
                raw,
                &format!("{identity}:call"),
                state
                    .pointer("/time/start")
                    .or_else(|| raw.pointer("/time/ran"))
                    .or(timestamp),
                part(
                    "tool_call",
                    "assistant",
                    name,
                    text_value(
                        state
                            .get("input")
                            .or_else(|| state.get("raw"))
                            .unwrap_or(&Value::Null),
                    ),
                ),
            )?;
            let status = state.get("status").and_then(Value::as_str).unwrap_or("");
            if matches!(status, "completed" | "error") {
                let output = state
                    .get("output")
                    .or_else(|| state.get("error"))
                    .or_else(|| state.get("content"))
                    .or_else(|| state.get("result"))
                    .unwrap_or(state);
                push_part(
                    builder,
                    info,
                    raw,
                    &format!("{identity}:result"),
                    state
                        .pointer("/time/end")
                        .or_else(|| raw.pointer("/time/completed"))
                        .or(timestamp),
                    part("tool_result", "tool", name, text_value(output)),
                )?;
            }
            Ok(())
        }
        _ => push_part(
            builder,
            info,
            raw,
            identity,
            timestamp,
            part("activity", role, typ, text_value(raw)),
        ),
    }
}

fn table_exists(db: &Connection, table: &str) -> Result<bool, String> {
    db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name=?1)",
        [table],
        |row| row.get(0),
    )
    .map_err(|error| error.to_string())
}

fn json_data(record: &Value) -> Result<Value, String> {
    let value = match record.get("data") {
        Some(Value::String(text)) => serde_json::from_str::<Value>(text)
            .map_err(|_| "An OpenCode data field is invalid JSON")?,
        Some(value) => value.clone(),
        None => return Err("An OpenCode record has no data field".into()),
    };
    if !value.is_object() {
        return Err("An OpenCode data field must be an object".into());
    }
    Ok(value)
}

fn read_records(
    db: &Connection,
    sql: &str,
    parameter: &str,
    columns: &[String],
    budget: &mut usize,
    cancelled: &dyn Fn() -> bool,
) -> Result<Vec<Value>, String> {
    let mut statement = db.prepare(sql).map_err(|error| error.to_string())?;
    let mut rows = statement
        .query([parameter])
        .map_err(|error| error.to_string())?;
    let mut records = vec![];
    while let Some(row) = rows.next().map_err(|error| error.to_string())? {
        check_cancelled(cancelled)?;
        if records.len() >= MAX_ENTRIES {
            return Err(
                "The OpenCode source exceeds the 50,000 record limit. Export selected sessions."
                    .into(),
            );
        }
        records.push(sql_record(row, columns, budget)?);
    }
    Ok(records)
}

pub(super) fn prepare_database(
    db: &Connection,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    let session_table = if table_exists(db, "session")? {
        "session"
    } else {
        "session_v2"
    };
    let session_columns = table_columns(
        db,
        "OpenCode",
        session_table,
        &["id", "title", "directory", "time_created"],
    )?;
    let legacy_columns = if table_exists(db, "message")? {
        Some(table_columns(
            db,
            "OpenCode",
            "message",
            &["id", "session_id", "data", "time_created"],
        )?)
    } else {
        None
    };
    let part_columns = if legacy_columns.is_some() {
        Some(table_columns(
            db,
            "OpenCode",
            "part",
            &["id", "message_id", "session_id", "data", "time_created"],
        )?)
    } else {
        None
    };
    let modern_columns = if table_exists(db, "session_message")? {
        Some(table_columns(
            db,
            "OpenCode",
            "session_message",
            &["id", "session_id", "type", "seq", "data", "time_created"],
        )?)
    } else {
        None
    };
    if legacy_columns.is_none() && modern_columns.is_none() {
        return Err(
            "The OpenCode database has no supported message tables. Export sessions as JSON."
                .into(),
        );
    }
    let project_columns = if table_exists(db, "project")? {
        Some(table_columns(db, "OpenCode", "project", &["id", "worktree"])?)
    } else {
        None
    };
    let mut sessions_statement = db
        .prepare(&format!(
            "SELECT * FROM {session_table} ORDER BY time_created,id LIMIT 1001"
        ))
        .map_err(|error| error.to_string())?;
    let mut rows = sessions_statement
        .query([])
        .map_err(|error| error.to_string())?;
    let (mut conversations, mut entries, mut bytes, mut budget, mut messages_read) =
        (vec![], 0, 0, 0, 0);
    while let Some(row) = rows.next().map_err(|error| error.to_string())? {
        check_cancelled(cancelled)?;
        if conversations.len() >= MAX_CONVERSATIONS {
            return Err("The OpenCode database exceeds the 1,000 conversation import limit. Export selected sessions.".into());
        }
        let session = sql_record(row, &session_columns, &mut budget)?;
        let id = scalar_id(session.get("id")).ok_or("An OpenCode session has no ID")?;
        let title = session
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("OpenCode conversation")
            .to_owned();
        let candidate = (|| {
            let mut header = session.clone();
            header["time"] =
                json!({"created":session["time_created"],"updated":session["time_updated"]});
            if let (Some(columns), Some(project_id)) =
                (&project_columns, scalar_id(session.get("project_id")))
            {
                if let Some(project) = read_records(
                    db,
                    "SELECT * FROM project WHERE id=?1",
                    &project_id,
                    columns,
                    &mut budget,
                    cancelled,
                )?
                .into_iter()
                .next()
                {
                    projects::attach_opencode_project(&mut header, &project);
                }
            }
            let mut messages = vec![];
            if let (Some(columns), Some(part_columns)) = (&legacy_columns, &part_columns) {
                for record in read_records(db, "SELECT * FROM message WHERE session_id=?1 ORDER BY time_created,id LIMIT 50001", &id, columns, &mut budget, cancelled)? {
                    let mut info = json_data(&record)?;
                    info["id"] = record["id"].clone(); info["sessionID"] = json!(id);
                    if info.get("time").is_none() { info["time"] = json!({"created":record["time_created"]}); }
                    info["sourceDatabaseMetadata"] = json!({"time_created":record["time_created"],"time_updated":record["time_updated"]});
                    let message_id = scalar_id(record.get("id")).ok_or("An OpenCode message has no ID")?;
                    let mut parts = vec![];
                    for record in read_records(db, "SELECT * FROM part WHERE message_id=?1 ORDER BY time_created,id LIMIT 50001", &message_id, part_columns, &mut budget, cancelled)? {
                        if record.get("session_id").and_then(Value::as_str) != Some(id.as_str()) { return Err("An OpenCode part belongs to another session".into()); }
                        let mut part = json_data(&record)?;
                        part["id"] = record["id"].clone(); part["messageID"] = json!(message_id);
                        part["sessionID"] = json!(id);
                        if part.get("time").is_none() { part["time"] = json!({"created":record["time_created"]}); }
                        part["sourceDatabaseMetadata"] = json!({"time_created":record["time_created"],"time_updated":record["time_updated"]});
                        parts.push(part);
                    }
                    messages.push(json!({"info":info,"parts":parts}));
                }
            }
            if let Some(columns) = &modern_columns {
                for record in read_records(
                    db,
                    "SELECT * FROM session_message WHERE session_id=?1 ORDER BY seq,id LIMIT 50001",
                    &id,
                    columns,
                    &mut budget,
                    cancelled,
                )? {
                    let mut message = json_data(&record)?;
                    message["id"] = record["id"].clone();
                    message["sessionID"] = json!(id);
                    message["type"] = record["type"].clone();
                    if message.get("time").is_none() {
                        message["time"] = json!({"created":record["time_created"]});
                    }
                    message["sourceDatabaseMetadata"] = json!({"seq":record["seq"],"time_created":record["time_created"],"time_updated":record["time_updated"]});
                    let message_id =
                        scalar_id(record.get("id")).ok_or("An OpenCode message has no ID")?;
                    // A migrated row supersedes its same-ID legacy projection without losing other legacy messages.
                    messages.retain(|message| {
                        message.pointer("/info/id").and_then(Value::as_str)
                            != Some(message_id.as_str())
                    });
                    messages.push(message);
                }
            }
            messages_read += messages.len();
            if messages_read > MAX_ENTRIES {
                return Err("The OpenCode database exceeds the 50,000 message limit. Export selected sessions.".into());
            }
            messages.sort_by(|left, right| {
                recorded_time(message_info(left))
                    .and_then(Value::as_i64)
                    .cmp(&recorded_time(message_info(right)).and_then(Value::as_i64))
            });
            parse_export(json!({"info":header,"messages":messages}), cancelled)
        })();
        let candidate = match candidate {
            Ok(candidate) => candidate,
            Err(error) if error == IMPORT_CANCELLED => return Err(error),
            Err(error) if messages_read > MAX_ENTRIES || budget as u64 > MAX_TEXT_BYTES => {
                return Err(error)
            }
            Err(error) => failed_candidate("opencode", &id, &title, error),
        };
        append_candidate(&mut conversations, &mut entries, &mut bytes, candidate)?;
    }
    Ok(PreparedFile {
        source_format: "opencode".into(),
        conversations,
    })
}
