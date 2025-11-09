// tuiDO - A TUI-based Todo application
// Entry point for the application

mod app;
mod event;
mod models;
mod storage;
mod ui;

use crossterm::{
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use std::io;

fn main() -> anyhow::Result<()> {
    // Initialize the terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // Create and run the app
    let mut app = app::App::new();
    let result = app.run(&mut terminal);

    // Cleanup and restore terminal on exit
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    // Handle any errors that occurred during app execution
    if let Err(err) = result {
        eprintln!("Error: {:?}", err);
    }

    Ok(())
}
