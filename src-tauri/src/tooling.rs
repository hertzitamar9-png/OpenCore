use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

pub(crate) fn read_only_tool_specs() -> Vec<Value> {
    vec![
        json!({"type":"function","function":{
            "name":"read_project_file",
            "description":"Read numbered lines from an existing text file inside the selected project's folder.",
            "parameters":{"type":"object","properties":{
                "path":{"type":"string","description":"Path relative to the selected project folder"},
                "start_line":{"type":"integer","minimum":1},
                "max_lines":{"type":"integer","minimum":1,"maximum":400}
            },"required":["path"],"additionalProperties":false}
        }}),
        json!({"type":"function","function":{
            "name":"search_project",
            "description":"Search text files inside the selected project's folder for an exact case-insensitive string.",
            "parameters":{"type":"object","properties":{
                "query":{"type":"string","description":"Literal text to find"}
            },"required":["query"],"additionalProperties":false}
        }}),
    ]
}

fn root_path(root: &Path) -> Result<PathBuf, String> {
    root.canonicalize().map_err(|error| format!("Project folder is unavailable: {error}"))
}

fn confined_file(root: &Path, raw: &str) -> Result<PathBuf, String> {
    if raw.trim().is_empty() {
        return Err("File path is required".into());
    }
    let canonical_root = root_path(root)?;
    let proposed = root.join(raw);
    let file = proposed.canonicalize().map_err(|error| format!("File is unavailable: {error}"))?;
    if !file.starts_with(&canonical_root) {
        return Err("File is outside the selected project folder".into());
    }
    if !file.is_file() {
        return Err("Path is not a file".into());
    }
    Ok(file)
}

fn relative_name(root: &Path, file: &Path) -> String {
    file.strip_prefix(root).unwrap_or(file).to_string_lossy().replace('\\', "/")
}

pub(crate) fn auto_review_read_only(root: &Path, name: &str, args: &Value) -> Result<(), String> {
    match name {
        "read_project_file" => {
            let path = args.get("path").and_then(Value::as_str).ok_or("path is required")?;
            confined_file(root, path)?;
            Ok(())
        }
        "search_project" => {
            root_path(root)?;
            let query = args.get("query").and_then(Value::as_str).unwrap_or("").trim();
            if query.is_empty() || query.len() > 200 { return Err("Search query must contain 1–200 characters".into()); }
            Ok(())
        }
        _ => Err(format!("Automatic review does not allow {name}")),
    }
}

pub(crate) fn execute_read_only(root: &Path, name: &str, args: &Value) -> Result<Value, String> {
    let canonical_root = root_path(root)?;
    match name {
        "read_project_file" => {
            let raw = args.get("path").and_then(Value::as_str).ok_or("path is required")?;
            let file = confined_file(&canonical_root, raw)?;
            if file.metadata().map_err(|error| error.to_string())?.len() > 4 * 1024 * 1024 {
                return Err("File exceeds the 4 MiB tool-read limit; use a smaller source file".into());
            }
            let bytes = std::fs::read(&file).map_err(|error| error.to_string())?;
            let text = String::from_utf8(bytes).map_err(|_| "File is not UTF-8 text")?;
            let start = args.get("start_line").and_then(Value::as_u64).unwrap_or(1).max(1) as usize;
            let max_lines = args.get("max_lines").and_then(Value::as_u64).unwrap_or(200).clamp(1, 400) as usize;
            let all_lines: Vec<&str> = text.lines().collect();
            let selected = all_lines.iter().enumerate().skip(start - 1).take(max_lines)
                .map(|(index, line)| format!("{}: {}", index + 1, line)).collect::<Vec<_>>().join("\n");
            Ok(json!({"path":relative_name(&canonical_root, &file),"start_line":start,
                      "total_lines":all_lines.len(),"truncated":start - 1 + max_lines < all_lines.len(),
                      "content":selected}))
        }
        "search_project" => {
            let query = args.get("query").and_then(Value::as_str).unwrap_or("").trim();
            if query.is_empty() || query.len() > 200 {
                return Err("Search query must contain 1–200 characters".into());
            }
            let needle = query.to_lowercase();
            let mut dirs = vec![canonical_root.clone()];
            let mut visited = HashSet::new();
            let mut scanned = 0usize;
            let mut matches = Vec::new();
            while let Some(dir) = dirs.pop() {
                if !visited.insert(dir.clone()) { continue; }
                let entries = std::fs::read_dir(&dir).map_err(|error| error.to_string())?;
                for entry in entries.flatten() {
                    let path = entry.path();
                    let Ok(kind) = entry.file_type() else { continue };
                    if kind.is_symlink() { continue; }
                    let Ok(real) = path.canonicalize() else { continue; };
                    if !real.starts_with(&canonical_root) { continue; }
                    if kind.is_dir() {
                        let name = entry.file_name().to_string_lossy().to_lowercase();
                        if !matches!(name.as_str(), ".git" | "node_modules" | "target" | ".venv" | "dist" | "build") {
                            dirs.push(real);
                        }
                        continue;
                    }
                    if !kind.is_file() { continue; }
                    scanned += 1;
                    if scanned > 5000 { break; }
                    if entry.metadata().map(|m| m.len() > 2 * 1024 * 1024).unwrap_or(true) { continue; }
                    let Ok(content) = std::fs::read_to_string(&real) else { continue; };
                    for (index, line) in content.lines().enumerate() {
                        if line.to_lowercase().contains(&needle) {
                            matches.push(json!({"path":relative_name(&canonical_root, &real),
                                                "line":index + 1,"preview":line.chars().take(240).collect::<String>()}));
                            if matches.len() >= 80 { break; }
                        }
                    }
                    if matches.len() >= 80 { break; }
                }
                if scanned > 5000 || matches.len() >= 80 { break; }
            }
            Ok(json!({"query":query,"matches":matches,"scanned_files":scanned,
                      "truncated":scanned > 5000 || matches.len() >= 80}))
        }
        _ => Err(format!("Unsupported or unauthorized tool: {name}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn reading_and_searching_stay_inside_the_selected_project() {
        let temp = std::env::temp_dir().join(format!("opencore-tool-test-{}", uuid::Uuid::new_v4()));
        let root = temp.join("project");
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/app.py"), "first line\nmagic needle\n").unwrap();
        std::fs::write(temp.join("secret.txt"), "outside project").unwrap();

        let read = execute_read_only(&root, "read_project_file", &json!({"path":"src/app.py"})).unwrap();
        assert!(read["content"].as_str().unwrap().contains("magic needle"));
        let found = execute_read_only(&root, "search_project", &json!({"query":"magic needle"})).unwrap();
        assert_eq!(found["matches"][0]["path"], "src/app.py");
        assert_eq!(found["matches"][0]["line"], 2);
        assert!(execute_read_only(&root, "read_project_file", &json!({"path":"../secret.txt"})).is_err());
        assert!(execute_read_only(&root, "read_project_file", &json!({"path":temp.join("secret.txt")})).is_err());
        assert!(execute_read_only(&root, "terminal", &json!({"cmd":"echo danger"})).is_err());
        std::fs::remove_dir_all(&temp).unwrap();
    }

    #[test]
    fn tool_specs_contain_only_executable_functions() {
        let names: Vec<String> = read_only_tool_specs().iter()
            .map(|tool| tool["function"]["name"].as_str().unwrap().to_string()).collect();
        assert_eq!(names, vec!["read_project_file", "search_project"]);
    }

    #[test]
    fn oversized_project_file_is_rejected_without_loading_it() {
        let temp = std::env::temp_dir().join(format!("opencore-tool-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp).unwrap();
        let file = std::fs::File::create(temp.join("large.txt")).unwrap();
        file.set_len(4 * 1024 * 1024 + 1).unwrap();
        let error = execute_read_only(&temp, "read_project_file", &json!({"path":"large.txt"})).unwrap_err();
        assert!(error.contains("4 MiB"));
        std::fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn automatic_review_accepts_only_bounded_read_and_search_calls() {
        let temp = std::env::temp_dir().join(format!("opencore-review-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp).unwrap();
        std::fs::write(temp.join("safe.txt"), "safe").unwrap();
        assert!(auto_review_read_only(&temp, "read_project_file", &json!({"path":"safe.txt"})).is_ok());
        assert!(auto_review_read_only(&temp, "search_project", &json!({"query":"safe"})).is_ok());
        assert!(auto_review_read_only(&temp, "read_project_file", &json!({"path":"../outside"})).is_err());
        assert!(auto_review_read_only(&temp, "terminal", &json!({"cmd":"echo hello"})).is_err());
        std::fs::remove_dir_all(temp).unwrap();
    }
}
