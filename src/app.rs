// App module - Main application state and logic
// This module will contain the App struct that manages:
// - Todo list state
// - Current selection/cursor position
// - Input mode (normal, insert, etc.)
// - Application state machine

use crate::models::Todo;
use crate::storage::FileStorage;
use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use std::io::Stdout;
use chrono::{Local, NaiveDate, Datelike};

#[derive(Debug, Clone, PartialEq)]
pub enum InputMode {
    Normal,
    EditingTitle,
    EditingDescription,
    EditingDate,
    DonePanel,
    DeletePanel,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Panel {
    List,
    Calendar,
    Task,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Tab {
    Tasks,
    Stats,
}

impl Tab {
    pub fn next(&self) -> Self {
        match self {
            Tab::Tasks => Tab::Stats,
            Tab::Stats => Tab::Tasks,
        }
    }

    pub fn previous(&self) -> Self {
        match self {
            Tab::Tasks => Tab::Stats,
            Tab::Stats => Tab::Tasks,
        }
    }
}

impl Panel {
    pub fn next(&self) -> Self {
        match self {
            Panel::List => Panel::Calendar,
            Panel::Calendar => Panel::Task,
            Panel::Task => Panel::List,
        }
    }
}

pub struct App {
    pub should_quit: bool,
    pub current_date: NaiveDate,
    pub todos: Vec<Todo>,
    pub show_new_task_panel: bool,
    pub show_done_panel: bool,
    pub done_panel_yes_selected: bool,
    pub completing_todo_id: Option<usize>,
    pub show_delete_panel: bool,
    pub delete_panel_yes_selected: bool,
    pub deleting_todo_id: Option<usize>,
    pub input_mode: InputMode,
    pub focused_panel: Panel,
    pub selected_tab: Tab,
    pub selected_todo_index: Option<usize>,
    pub selected_calendar_date: Option<NaiveDate>,
    pub task_description_scroll: u16,
    pub edit_description_scroll: u16,
    pub editing_todo_id: Option<usize>,
    pub new_task_title: String,
    pub new_task_description: String,
    pub new_task_due_date: Option<NaiveDate>,
    pub date_input_buffer: String,
    storage: FileStorage,
}

impl App {
    pub fn new() -> Self {
        let storage = FileStorage::new(FileStorage::get_default_path());
        let all_todos = storage.load_todos().unwrap_or_else(|_| Vec::new());
        // Filter out completed and deleted todos
        let todos: Vec<Todo> = all_todos.into_iter().filter(|t| !t.completed && !t.deleted).collect();
        let selected_todo_index = if todos.is_empty() { None } else { Some(0) };

        let mut app = Self {
            should_quit: false,
            current_date: Local::now().date_naive(),
            todos,
            show_new_task_panel: false,
            show_done_panel: false,
            done_panel_yes_selected: true,
            completing_todo_id: None,
            show_delete_panel: false,
            delete_panel_yes_selected: true,
            deleting_todo_id: None,
            input_mode: InputMode::Normal,
            focused_panel: Panel::List,
            selected_tab: Tab::Tasks,
            selected_todo_index,
            selected_calendar_date: None,
            task_description_scroll: 0,
            edit_description_scroll: 0,
            editing_todo_id: None,
            new_task_title: String::new(),
            new_task_description: String::new(),
            new_task_due_date: None,
            date_input_buffer: String::new(),
            storage,
        };

        app.sort_todos();
        app
    }

    pub fn next_panel(&mut self) {
        self.focused_panel = self.focused_panel.next();

        // Initialize calendar selection to today when switching to calendar panel
        if self.focused_panel == Panel::Calendar && self.selected_calendar_date.is_none() {
            self.selected_calendar_date = Some(Local::now().date_naive());
        }
    }

    pub fn next_tab(&mut self) {
        self.selected_tab = self.selected_tab.next();
    }

    pub fn previous_tab(&mut self) {
        self.selected_tab = self.selected_tab.previous();
    }

    fn sort_todos(&mut self) {
        self.todos.sort_by(|a, b| {
            // First sort by due date (ascending, None comes last)
            match (a.due_date, b.due_date) {
                (Some(date_a), Some(date_b)) => {
                    // Both have due dates, compare them
                    match date_a.cmp(&date_b) {
                        std::cmp::Ordering::Equal => {
                            // If due dates are equal, sort by created date
                            a.created_at.cmp(&b.created_at)
                        }
                        other => other,
                    }
                }
                (Some(_), None) => std::cmp::Ordering::Less,  // Tasks with due dates come first
                (None, Some(_)) => std::cmp::Ordering::Greater, // Tasks without due dates come last
                (None, None) => a.created_at.cmp(&b.created_at), // Both have no due date, sort by created
            }
        });
    }

    pub fn select_previous_todo(&mut self) {
        if self.todos.is_empty() {
            self.selected_todo_index = None;
            return;
        }

        self.selected_todo_index = Some(match self.selected_todo_index {
            Some(i) if i > 0 => i - 1,
            Some(_) => self.todos.len() - 1,
            None => 0,
        });

        // Reset scroll when changing tasks
        self.task_description_scroll = 0;
    }

    pub fn select_next_todo(&mut self) {
        if self.todos.is_empty() {
            self.selected_todo_index = None;
            return;
        }

        self.selected_todo_index = Some(match self.selected_todo_index {
            Some(i) if i < self.todos.len() - 1 => i + 1,
            Some(_) => 0,
            None => 0,
        });

        // Reset scroll when changing tasks
        self.task_description_scroll = 0;
    }

    pub fn select_next_day(&mut self) {
        if let Some(date) = self.selected_calendar_date {
            self.selected_calendar_date = Some(date + chrono::Duration::days(1));
            self.update_calendar_view();
        } else {
            self.selected_calendar_date = Some(Local::now().date_naive());
        }
    }

    pub fn select_previous_day(&mut self) {
        if let Some(date) = self.selected_calendar_date {
            self.selected_calendar_date = Some(date - chrono::Duration::days(1));
            self.update_calendar_view();
        } else {
            self.selected_calendar_date = Some(Local::now().date_naive());
        }
    }

    pub fn select_day_above(&mut self) {
        if let Some(date) = self.selected_calendar_date {
            self.selected_calendar_date = Some(date - chrono::Duration::days(7));
            self.update_calendar_view();
        } else {
            self.selected_calendar_date = Some(Local::now().date_naive());
        }
    }

    pub fn select_day_below(&mut self) {
        if let Some(date) = self.selected_calendar_date {
            self.selected_calendar_date = Some(date + chrono::Duration::days(7));
            self.update_calendar_view();
        } else {
            self.selected_calendar_date = Some(Local::now().date_naive());
        }
    }

    fn update_calendar_view(&mut self) {
        // Check if selected date is outside the visible range and shift the view if needed
        if let Some(selected) = self.selected_calendar_date {
            let current_year = self.current_date.year();
            let current_month = self.current_date.month();

            let selected_year = selected.year();
            let selected_month = selected.month();

            // Calculate the first month of the visible range (previous month)
            let (prev_year, prev_month) = if current_month == 1 {
                (current_year - 1, 12)
            } else {
                (current_year, current_month - 1)
            };

            // Calculate the last month of the visible range (next month)
            let (next_year, next_month) = if current_month == 12 {
                (current_year + 1, 1)
            } else {
                (current_year, current_month + 1)
            };

            // Check if selected date is before the visible range
            if selected_year < prev_year || (selected_year == prev_year && selected_month < prev_month) {
                // Shift view backward by one month
                self.current_date = if current_month == 1 {
                    NaiveDate::from_ymd_opt(current_year - 1, 12, 1).unwrap()
                } else {
                    NaiveDate::from_ymd_opt(current_year, current_month - 1, 1).unwrap()
                };
            }
            // Check if selected date is after the visible range
            else if selected_year > next_year || (selected_year == next_year && selected_month > next_month) {
                // Shift view forward by one month
                self.current_date = if current_month == 12 {
                    NaiveDate::from_ymd_opt(current_year + 1, 1, 1).unwrap()
                } else {
                    NaiveDate::from_ymd_opt(current_year, current_month + 1, 1).unwrap()
                };
            }
        }
    }

    pub fn reset_calendar_to_today(&mut self) {
        let today = Local::now().date_naive();
        self.current_date = today;
        self.selected_calendar_date = Some(today);
    }

    pub fn scroll_description_up(&mut self) {
        if self.task_description_scroll > 0 {
            self.task_description_scroll -= 1;
        }
    }

    pub fn scroll_description_down(&mut self) {
        self.task_description_scroll += 1;
    }

    pub fn scroll_edit_description_up(&mut self) {
        self.edit_description_scroll = self.edit_description_scroll.saturating_sub(3);
    }

    pub fn scroll_edit_description_down(&mut self) {
        self.edit_description_scroll = self.edit_description_scroll.saturating_add(3);
    }

    pub fn auto_scroll_to_cursor(&mut self) {
        // Auto-scroll the description view to keep the cursor visible
        // Estimate visible lines: panel is 70% height, description gets Min(10) lines
        // With typical terminal height, we can see about 10-15 lines
        let visible_lines = 10u16;

        let line_count = self.new_task_description.split('\n').count() as u16;

        // If content exceeds visible area, scroll to show the bottom
        if line_count > visible_lines {
            // Keep cursor near bottom with 1 line padding
            self.edit_description_scroll = (line_count - visible_lines + 1).max(0);
        } else {
            // Content fits, no scroll needed
            self.edit_description_scroll = 0;
        }
    }

    pub fn get_all_todos(&self) -> Vec<Todo> {
        self.storage.load_todos().unwrap_or_else(|_| Vec::new())
    }

    pub fn open_new_task_panel(&mut self) {
        self.open_new_task_panel_with_date(None);
    }

    pub fn open_new_task_panel_with_date(&mut self, due_date: Option<NaiveDate>) {
        self.show_new_task_panel = true;
        self.input_mode = InputMode::EditingTitle;
        self.editing_todo_id = None;
        self.new_task_title.clear();
        self.new_task_description.clear();
        self.new_task_due_date = due_date;
        self.date_input_buffer = due_date
            .map(|d| d.format("%Y-%m-%d").to_string())
            .unwrap_or_else(|| String::new());
        self.edit_description_scroll = 0;
    }

    pub fn open_edit_task_panel(&mut self) {
        if let Some(index) = self.selected_todo_index {
            if let Some(todo) = self.todos.get(index) {
                self.show_new_task_panel = true;
                self.input_mode = InputMode::EditingTitle;
                self.editing_todo_id = Some(todo.id);
                self.new_task_title = todo.title.clone();
                self.new_task_description = todo.description.clone();
                self.new_task_due_date = todo.due_date;
                self.date_input_buffer = todo.due_date
                    .map(|d| d.format("%Y-%m-%d").to_string())
                    .unwrap_or_else(|| String::new());
                self.edit_description_scroll = 0;
            }
        }
    }

    pub fn close_new_task_panel(&mut self) {
        self.show_new_task_panel = false;
        self.input_mode = InputMode::Normal;
        self.editing_todo_id = None;
        self.new_task_title.clear();
        self.new_task_description.clear();
        self.new_task_due_date = None;
        self.date_input_buffer.clear();
    }

    pub fn open_done_panel(&mut self) {
        if let Some(index) = self.selected_todo_index {
            if let Some(todo) = self.todos.get(index) {
                self.show_done_panel = true;
                self.completing_todo_id = Some(todo.id);
                self.done_panel_yes_selected = true;
                self.input_mode = InputMode::DonePanel;
            }
        }
    }

    pub fn close_done_panel(&mut self) {
        self.show_done_panel = false;
        self.completing_todo_id = None;
        self.done_panel_yes_selected = true;
        self.input_mode = InputMode::Normal;
    }

    pub fn toggle_done_button(&mut self) {
        self.done_panel_yes_selected = !self.done_panel_yes_selected;
    }

    pub fn mark_task_complete(&mut self) {
        if let Some(completing_id) = self.completing_todo_id {
            // Load all todos (including completed ones)
            let mut all_todos = self.storage.load_todos().unwrap_or_else(|_| Vec::new());

            // Find and mark the task as complete
            if let Some(todo) = all_todos.iter_mut().find(|t| t.id == completing_id) {
                todo.toggle_completed();
            }

            // Save all todos (including the newly completed one)
            let _ = self.storage.save_todos(&all_todos);

            // Remove the completed task from the current display list
            self.todos.retain(|t| t.id != completing_id);

            // Adjust selected index if needed
            if self.todos.is_empty() {
                self.selected_todo_index = None;
            } else if let Some(index) = self.selected_todo_index {
                if index >= self.todos.len() {
                    self.selected_todo_index = Some(self.todos.len() - 1);
                }
            }
        }
        self.close_done_panel();
    }

    pub fn open_delete_panel(&mut self) {
        if let Some(index) = self.selected_todo_index {
            if let Some(todo) = self.todos.get(index) {
                self.show_delete_panel = true;
                self.deleting_todo_id = Some(todo.id);
                self.delete_panel_yes_selected = true;
                self.input_mode = InputMode::DeletePanel;
            }
        }
    }

    pub fn close_delete_panel(&mut self) {
        self.show_delete_panel = false;
        self.deleting_todo_id = None;
        self.delete_panel_yes_selected = true;
        self.input_mode = InputMode::Normal;
    }

    pub fn toggle_delete_button(&mut self) {
        self.delete_panel_yes_selected = !self.delete_panel_yes_selected;
    }

    pub fn mark_task_deleted(&mut self) {
        if let Some(deleting_id) = self.deleting_todo_id {
            // Load all todos (including completed and deleted ones)
            let mut all_todos = self.storage.load_todos().unwrap_or_else(|_| Vec::new());

            // Find and mark the task as deleted
            if let Some(todo) = all_todos.iter_mut().find(|t| t.id == deleting_id) {
                todo.mark_deleted();
            }

            // Save all todos (including the newly deleted one)
            let _ = self.storage.save_todos(&all_todos);

            // Remove the deleted task from the current display list
            self.todos.retain(|t| t.id != deleting_id);

            // Adjust selected index if needed
            if self.todos.is_empty() {
                self.selected_todo_index = None;
            } else if let Some(index) = self.selected_todo_index {
                if index >= self.todos.len() {
                    self.selected_todo_index = Some(self.todos.len() - 1);
                }
            }
        }
        self.close_delete_panel();
    }

    pub fn save_new_task(&mut self) {
        if !self.new_task_title.is_empty() {
            let task_id = if let Some(editing_id) = self.editing_todo_id {
                // Edit existing todo
                if let Some(todo) = self.todos.iter_mut().find(|t| t.id == editing_id) {
                    todo.title = self.new_task_title.clone();
                    todo.description = self.new_task_description.clone();
                    todo.due_date = self.new_task_due_date;
                }
                editing_id
            } else {
                // Create new todo
                let new_id = self.todos.iter().map(|t| t.id).max().unwrap_or(0) + 1;
                let todo = Todo::new(
                    new_id,
                    self.new_task_title.clone(),
                    self.new_task_description.clone(),
                    self.new_task_due_date,
                );
                self.todos.push(todo);
                new_id
            };

            // Sort todos after adding/editing
            self.sort_todos();

            // Update selected index to point to the edited/added task after sorting
            self.selected_todo_index = self.todos.iter().position(|t| t.id == task_id);

            // Persist to file
            let _ = self.storage.save_todos(&self.todos);
        }
        self.close_new_task_panel();
    }

    pub fn run(&mut self, terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> anyhow::Result<()> {
        loop {
            // Render the UI
            terminal.draw(|frame| crate::ui::render(frame, self))?;

            // Handle events
            if event::poll(std::time::Duration::from_millis(100))? {
                if let Event::Key(key) = event::read()? {
                    self.handle_key_event(key);
                }
            }

            if self.should_quit {
                break;
            }
        }
        Ok(())
    }

    fn handle_key_event(&mut self, key: crossterm::event::KeyEvent) {
        match self.input_mode {
            InputMode::Normal => {
                match key.code {
                    KeyCode::Char('q') => self.should_quit = true,
                    KeyCode::Char('+') => self.open_new_task_panel(),
                    KeyCode::Tab => self.next_panel(),
                    KeyCode::Esc => self.should_quit = true,
                    KeyCode::Left => {
                        if key.modifiers.contains(KeyModifiers::SHIFT) {
                            self.previous_tab();
                        } else if self.focused_panel == Panel::Calendar {
                            self.select_previous_day();
                        }
                    }
                    KeyCode::Right => {
                        if key.modifiers.contains(KeyModifiers::SHIFT) {
                            self.next_tab();
                        } else if self.focused_panel == Panel::Calendar {
                            self.select_next_day();
                        }
                    }
                    KeyCode::Up => {
                        if self.focused_panel == Panel::List {
                            self.select_previous_todo();
                        } else if self.focused_panel == Panel::Calendar {
                            self.select_day_above();
                        } else if self.focused_panel == Panel::Task {
                            self.scroll_description_up();
                        }
                    }
                    KeyCode::Down => {
                        if self.focused_panel == Panel::List {
                            self.select_next_todo();
                        } else if self.focused_panel == Panel::Calendar {
                            self.select_day_below();
                        } else if self.focused_panel == Panel::Task {
                            self.scroll_description_down();
                        }
                    }
                    KeyCode::Enter => {
                        if self.focused_panel == Panel::List && self.selected_todo_index.is_some() {
                            self.open_edit_task_panel();
                        } else if self.focused_panel == Panel::Calendar {
                            self.open_new_task_panel_with_date(self.selected_calendar_date);
                        }
                    }
                    KeyCode::Char('d') => {
                        if self.focused_panel == Panel::List && self.selected_todo_index.is_some() {
                            self.open_done_panel();
                        }
                    }
                    KeyCode::Char('-') => {
                        if self.focused_panel == Panel::List && self.selected_todo_index.is_some() {
                            self.open_delete_panel();
                        }
                    }
                    KeyCode::Char('t') => {
                        if self.focused_panel == Panel::Calendar {
                            self.reset_calendar_to_today();
                        }
                    }
                    _ => {}
                }
            }
            InputMode::EditingTitle => {
                match key.code {
                    KeyCode::Char(c) => {
                        self.new_task_title.push(c);
                    }
                    KeyCode::Backspace => {
                        self.new_task_title.pop();
                    }
                    KeyCode::Tab => {
                        // Switch to description input
                        self.input_mode = InputMode::EditingDescription;
                    }
                    KeyCode::Enter => {
                        // Save the task
                        self.save_new_task();
                    }
                    KeyCode::Esc => {
                        self.close_new_task_panel();
                    }
                    _ => {}
                }
            }
            InputMode::EditingDescription => {
                match key.code {
                    KeyCode::Char(c) => {
                        if key.modifiers.contains(KeyModifiers::CONTROL) {
                            match c {
                                'u' => {
                                    // Ctrl+U: Scroll description view up
                                    self.scroll_edit_description_up();
                                }
                                'd' => {
                                    // Ctrl+D: Scroll description view down
                                    self.scroll_edit_description_down();
                                }
                                _ => {
                                    self.new_task_description.push(c);
                                    self.auto_scroll_to_cursor();
                                }
                            }
                        } else {
                            self.new_task_description.push(c);
                            self.auto_scroll_to_cursor();
                        }
                    }
                    KeyCode::Backspace => {
                        self.new_task_description.pop();
                        self.auto_scroll_to_cursor();
                    }
                    KeyCode::PageUp => {
                        // PageUp: Scroll description view up
                        self.scroll_edit_description_up();
                    }
                    KeyCode::PageDown => {
                        // PageDown: Scroll description view down
                        self.scroll_edit_description_down();
                    }
                    KeyCode::Tab => {
                        // Switch to date input
                        self.input_mode = InputMode::EditingDate;
                    }
                    KeyCode::Enter => {
                        if key.modifiers.contains(KeyModifiers::ALT) {
                            // Alt+Enter: Add newline to description
                            self.new_task_description.push('\n');
                            self.auto_scroll_to_cursor();
                        } else {
                            // Enter: Save the task
                            self.save_new_task();
                        }
                    }
                    KeyCode::Esc => {
                        self.close_new_task_panel();
                    }
                    _ => {}
                }
            }
            InputMode::EditingDate => {
                match key.code {
                    KeyCode::Char(c) if c.is_ascii_digit() || c == '-' => {
                        self.date_input_buffer.push(c);
                    }
                    KeyCode::Backspace => {
                        self.date_input_buffer.pop();
                    }
                    KeyCode::Tab => {
                        // Switch back to title input
                        self.input_mode = InputMode::EditingTitle;
                    }
                    KeyCode::Enter => {
                        // Try to parse the date
                        if let Ok(date) = NaiveDate::parse_from_str(&self.date_input_buffer, "%Y-%m-%d") {
                            self.new_task_due_date = Some(date);
                        }
                        // Save the task
                        self.save_new_task();
                    }
                    KeyCode::Esc => {
                        self.close_new_task_panel();
                    }
                    _ => {}
                }
            }
            InputMode::DonePanel => {
                match key.code {
                    KeyCode::Tab | KeyCode::Left | KeyCode::Right => {
                        self.toggle_done_button();
                    }
                    KeyCode::Enter => {
                        if self.done_panel_yes_selected {
                            self.mark_task_complete();
                        } else {
                            self.close_done_panel();
                        }
                    }
                    KeyCode::Esc => {
                        self.close_done_panel();
                    }
                    _ => {}
                }
            }
            InputMode::DeletePanel => {
                match key.code {
                    KeyCode::Tab | KeyCode::Left | KeyCode::Right => {
                        self.toggle_delete_button();
                    }
                    KeyCode::Enter => {
                        if self.delete_panel_yes_selected {
                            self.mark_task_deleted();
                        } else {
                            self.close_delete_panel();
                        }
                    }
                    KeyCode::Esc => {
                        self.close_delete_panel();
                    }
                    _ => {}
                }
            }
        }
    }
}
