# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

T-Dui is a TUI (Terminal User Interface) todo list application built in Rust using Ratatui. The project was "vibe coded" with ClaudeCode.

## Building and Running

```bash
# Build and run the application
cargo run

# Build in release mode
cargo build --release

# Run tests
cargo test

# Check for compilation errors without building
cargo check
```

## Architecture

### Core Modules

- **main.rs**: Entry point that initializes the terminal, creates the App instance, and handles cleanup
- **app.rs**: Contains the main `App` struct that manages all application state, input modes, panels, and business logic
- **ui.rs**: All rendering logic using Ratatui widgets (Lists, Calendars, Charts, Popups)
- **event.rs**: Event handling module (currently minimal, most event handling is in app.rs)
- **models/todo.rs**: The `Todo` data structure with JSON serialization
- **storage/file_storage.rs**: JSON-based file persistence to `~/.local/share/tdui/todos.json`

### State Management Pattern

The app follows a centralized state pattern where the `App` struct in app.rs holds all state:

- **Input Modes**: `Normal`, `EditingTitle`, `EditingDescription`, `EditingDate`, `DonePanel`, `DeletePanel`
- **Panels**: `List`, `Calendar`, `Task` (cyclable with Tab key)
- **Tabs**: `Tasks`, `Stats` (switchable with Shift+Arrow keys)

The main event loop is in `App::run()`:
1. Render UI via `ui::render()`
2. Poll for events with 100ms timeout
3. Handle key events via `App::handle_key_event()`
4. Loop until `should_quit` is true

### Data Flow

1. **Load**: `FileStorage::load_todos()` reads from JSON file into `Vec<Todo>`
2. **Filter**: Active todos (not completed, not deleted) are filtered for display
3. **Sort**: Todos are sorted by due date (ascending), then by created date
4. **Save**: Any modification triggers `FileStorage::save_todos()` to persist changes

Note: Completed and deleted tasks are kept in the JSON file but filtered out from the main display list (`app.todos`). Use `app.get_all_todos()` to access all tasks including completed/deleted.

### UI Layout (Tasks Tab)

```
┌─────────────────────────────────────────────┐
│ [Tasks] [Stats]                             │ ← Tabs
├─────────────┬───────────────────────────────┤
│             │ Calendar Panel (33%)          │
│   List      ├───────────────────────────────┤
│  Panel      │                               │
│  (33%)      │   Task Details Panel (67%)    │
│             │                               │
└─────────────┴───────────────────────────────┘
│ Footer with keybindings                     │
└─────────────────────────────────────────────┘
```

### UI Layout (Stats Tab)

Four stat panels (Overdue, ToDo, Done, Deleted) displayed with big text, followed by a chart showing "New Tasks" over the last 3 months with three datasets: Tasks Created (yellow), Overdue Tasks (red), Tasks Completed (cyan).

### Key Keybindings

- **Tab**: Cycle between panels (List → Calendar → Task → List)
- **Shift+Left/Right**: Switch between tabs
- **+**: Create new task
- **d**: Mark task as done (shows confirmation dialog)
- **-**: Delete task (shows confirmation dialog)
- **Enter**: Edit selected task (in List panel) or create task with due date (in Calendar panel)
- **t**: Reset calendar to today (in Calendar panel)
- **Arrow keys**: Navigate lists, calendar, or scroll description
- **Esc** or **q**: Quit application

When editing tasks:
- **Tab**: Cycle through input fields (Title → Description → Date → Title)
- **Enter**: Save task
- **Alt+Enter**: Add newline in description
- **Ctrl+U/D** or **PageUp/PageDown**: Scroll description view
- **Esc**: Cancel editing

## Key Implementation Details

### Task Completion and Deletion

Tasks are soft-deleted/completed:
- `Todo::toggle_completed()` sets `completed` flag and `completed_at` timestamp
- `Todo::mark_deleted()` sets `deleted` flag
- Both operations keep the task in the JSON file but filter it from display
- All todos (including completed/deleted) are loaded, modified, and saved together to maintain data integrity

### Calendar Integration

The app displays three months simultaneously (previous, current, next):
- Due dates are highlighted in dark gray
- Overdue tasks (due date before today and not completed) are highlighted in red
- Today is highlighted in cyan
- Selected date (when calendar is focused) is highlighted in yellow
- Calendar auto-scrolls when navigating beyond visible month range

### Sorting Logic

Tasks are sorted with this priority (in app.rs `sort_todos()`):
1. Tasks with due dates come before tasks without due dates
2. Among tasks with due dates, earlier dates come first
3. If due dates are equal, sort by created timestamp
4. Tasks without due dates are sorted by created timestamp

### Statistics Calculation

The Stats tab calculates:
- **Overdue count**: Tasks where `due_date < today` and not completed
- **ToDo count**: All tasks in `app.todos` (already filtered for active tasks)
- **Done count**: All tasks with `completed == true` from full todos list
- **Deleted count**: All tasks with `deleted == true` from full todos list
- **Charts**: Track tasks created, overdue, and completed over 90 days

### Color Coding

- **Red**: Overdue tasks
- **Yellow**: Tasks due today, ToDo count
- **Cyan**: Selected panel borders, today's date, completed count
- **Green**: Done status
- **Dark Gray**: Unfocused panel borders, due dates on calendar

## Dependencies

- **ratatui**: TUI framework with `widget-calendar` feature enabled
- **crossterm**: Terminal manipulation and event handling
- **serde/serde_json**: JSON serialization
- **chrono**: Date/time handling with `serde` feature
- **anyhow**: Error handling
- **time**: Required for calendar widget date conversion
- **tui-big-text**: Large text rendering for statistics

## File Structure

```
src/
├── main.rs                 # Entry point
├── app.rs                  # Application state and logic (729 lines)
├── ui.rs                   # All rendering code (1003 lines)
├── event.rs                # Event handling (currently minimal/unused)
├── models/
│   ├── mod.rs
│   └── todo.rs            # Todo data model
└── storage/
    ├── mod.rs
    └── file_storage.rs    # JSON persistence
```

## Data Storage

Todos are stored in: `~/.local/share/tdui/todos.json`

Format: JSON array of Todo objects with fields:
- `id`: usize
- `title`: String
- `description`: String
- `completed`: bool
- `deleted`: bool (defaults to false for backward compatibility)
- `created_at`: DateTime<Utc>
- `due_date`: Option<NaiveDate>
- `completed_at`: Option<DateTime<Utc>>

## Manual QA: Test Parties

- **`/write-test-plan`**: turns stories and acceptance criteria into journey-shaped CSV test cases in `docs/test-plans/`.
- **`/test-party`**: runs those cases as a Test Party, logging results per tester on a `test-party/` branch under `docs/test-parties/`.
- **`.claude/skills/test-party/party.py`**: the only writer of party results, configured by `.claude/skills/test-party/test-party.json`. `party.py check` validates the plans (CI runs it on pull requests).
