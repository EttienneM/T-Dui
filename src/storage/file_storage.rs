// File storage - JSON-based persistence for todos

use crate::models::Todo;
use std::path::PathBuf;
use std::fs;

pub struct FileStorage {
    file_path: PathBuf,
}

impl FileStorage {
    pub fn new(file_path: PathBuf) -> Self {
        Self { file_path }
    }

    pub fn load_todos(&self) -> anyhow::Result<Vec<Todo>> {
        // Check if file exists
        if !self.file_path.exists() {
            return Ok(Vec::new());
        }

        // Read file contents
        let contents = fs::read_to_string(&self.file_path)?;

        // Deserialize JSON to Vec<Todo>
        let todos: Vec<Todo> = serde_json::from_str(&contents)?;

        Ok(todos)
    }

    pub fn save_todos(&self, todos: &[Todo]) -> anyhow::Result<()> {
        // Create parent directory if it doesn't exist
        if let Some(parent) = self.file_path.parent() {
            fs::create_dir_all(parent)?;
        }

        // Serialize Vec<Todo> to JSON with pretty printing
        let json = serde_json::to_string_pretty(todos)?;

        // Write to file
        fs::write(&self.file_path, json)?;

        Ok(())
    }

    pub fn get_default_path() -> PathBuf {
        // Get home directory
        let home = std::env::var("HOME")
            .or_else(|_| std::env::var("USERPROFILE"))
            .unwrap_or_else(|_| ".".to_string());

        // Return path: ~/.local/share/tdui/todos.json
        PathBuf::from(home)
            .join(".local")
            .join("share")
            .join("tdui")
            .join("todos.json")
    }
}
