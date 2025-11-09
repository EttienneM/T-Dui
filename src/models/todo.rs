// Todo model - Represents a single todo item

use chrono::{DateTime, Utc, NaiveDate};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Todo {
    pub id: usize,
    pub title: String,
    pub description: String,
    pub completed: bool,
    #[serde(default)]
    pub deleted: bool,
    pub created_at: DateTime<Utc>,
    pub due_date: Option<NaiveDate>,
    pub completed_at: Option<DateTime<Utc>>,
}

impl Todo {
    pub fn new(id: usize, title: String, description: String, due_date: Option<NaiveDate>) -> Self {
        Self {
            id,
            title,
            description,
            completed: false,
            deleted: false,
            created_at: Utc::now(),
            due_date,
            completed_at: None,
        }
    }

    pub fn toggle_completed(&mut self) {
        self.completed = !self.completed;
        self.completed_at = if self.completed {
            Some(Utc::now())
        } else {
            None
        };
    }

    pub fn mark_deleted(&mut self) {
        self.deleted = true;
    }

    pub fn display_string(&self) -> String {
        if let Some(due_date) = self.due_date {
            format!("{} (Due: {})", self.title, due_date.format("%Y-%m-%d"))
        } else {
            self.title.clone()
        }
    }
}
