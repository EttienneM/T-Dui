// UI module - Rendering logic for the TUI
// This module will handle all the visual rendering using Ratatui

use ratatui::{
    Frame,
    layout::{Layout, Constraint, Direction, Rect, Alignment},
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Clear, Tabs, calendar::{Monthly, CalendarEventStore}, Chart, Dataset, Axis, GraphType},
    style::{Style, Color, Modifier},
    text::{Line, Span},
    symbols,
};
use chrono::{Datelike, NaiveDate, Local, Duration};
use time::{Date, Month};
use crate::app::{App, InputMode, Panel, Tab};
use tui_big_text::{BigText, PixelSize};

/// Helper function to get border style based on whether a panel is focused
fn get_border_style(is_focused: bool) -> Style {
    if is_focused {
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(Color::DarkGray)
    }
}

pub fn render(frame: &mut Frame, app: &App) {
    let size = frame.area();

    // Split the screen into tabs, main area, and footer
    let main_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),   // Tabs
            Constraint::Min(0),      // Main content area
            Constraint::Length(1),   // Footer
        ])
        .split(size);

    // Render tabs
    render_tabs(frame, app, main_layout[0]);

    // Render content based on selected tab
    match app.selected_tab {
        Tab::Tasks => render_tasks_tab(frame, app, main_layout[1]),
        Tab::Stats => render_stats_tab(frame, app, main_layout[1]),
    }

    // Render footer
    render_footer(frame, main_layout[2]);

    // Render the new task panel if it's open
    if app.show_new_task_panel {
        render_new_task_panel(frame, app);
    }

    // Render the done panel if it's open
    if app.show_done_panel {
        render_done_panel(frame, app);
    }

    // Render the delete panel if it's open
    if app.show_delete_panel {
        render_delete_panel(frame, app);
    }
}

fn render_tabs(frame: &mut Frame, app: &App, area: Rect) {
    let titles = vec!["Tasks", "Stats"];
    let selected_index = match app.selected_tab {
        Tab::Tasks => 0,
        Tab::Stats => 1,
    };

    let tabs = Tabs::new(titles)
        .block(Block::default().borders(Borders::ALL))
        .select(selected_index)
        .style(Style::default().fg(Color::White))
        .highlight_style(
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD)
        );

    frame.render_widget(tabs, area);
}

fn render_tasks_tab(frame: &mut Frame, app: &App, area: Rect) {
    // Main layout: Split into two vertical columns (1/3 left, 2/3 right)
    let main_columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(33),  // Left column (1/3)
            Constraint::Percentage(67),  // Right column (2/3)
        ])
        .split(area);

    // Split the right column horizontally (1/3 top, 2/3 bottom)
    let right_sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage(33),  // Top section (1/3)
            Constraint::Percentage(67),  // Bottom section (2/3)
        ])
        .split(main_columns[1]);

    // Create the task list widget
    let today = Local::now().date_naive();
    let task_items: Vec<ListItem> = app.todos
        .iter()
        .enumerate()
        .map(|(i, todo)| {
            let content = format!("{}. {}", i + 1, todo.display_string());

            // Determine task color based on due date
            if let Some(due_date) = todo.due_date {
                if !todo.completed {
                    if due_date < today {
                        // Overdue tasks in red
                        ListItem::new(content).style(Style::default().fg(Color::Red))
                    } else if due_date == today {
                        // Tasks due today in yellow
                        ListItem::new(content).style(Style::default().fg(Color::Yellow))
                    } else {
                        // Future tasks in default color
                        ListItem::new(content)
                    }
                } else {
                    // Completed tasks in default color
                    ListItem::new(content)
                }
            } else {
                // No due date in default color
                ListItem::new(content)
            }
        })
        .collect();

    let list_border_style = get_border_style(app.focused_panel == Panel::List);
    let task_list = List::new(task_items)
        .block(Block::default()
            .title("List")
            .borders(Borders::ALL)
            .border_style(list_border_style))
        .style(Style::default())
        .highlight_style(
            Style::default()
                .add_modifier(Modifier::BOLD)
        )
        .highlight_symbol(">> ");

    // Create list state for selection
    let mut list_state = ListState::default();
    list_state.select(app.selected_todo_index);

    // Render the widgets
    frame.render_stateful_widget(task_list, main_columns[0], &mut list_state);
    render_calendar(frame, app, right_sections[0]);
    render_task_details(frame, app, right_sections[1]);
}

fn render_stats_tab(frame: &mut Frame, app: &App, area: Rect) {
    let today = Local::now().date_naive();

    // Load all todos including completed and deleted ones
    let all_todos = app.get_all_todos();

    // Calculate statistics
    let overdue_count = app.todos.iter()
        .filter(|t| {
            if let Some(due_date) = t.due_date {
                due_date < today && !t.completed
            } else {
                false
            }
        })
        .count();

    // Count all pending (not completed, not deleted) tasks
    // Note: app.todos is already filtered to exclude completed and deleted tasks
    let todo_count = app.todos.len();

    // Count completed tasks
    let done_count = all_todos.iter()
        .filter(|t| t.completed)
        .count();

    // Count deleted tasks
    let deleted_count = all_todos.iter()
        .filter(|t| t.deleted)
        .count();

    // Divide into three equal rows
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage(33),  // Top row
            Constraint::Percentage(33),  // Middle row
            Constraint::Percentage(34),  // Bottom row
        ])
        .split(area);

    // Divide the top row into four equal panels
    let top_panels = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(25),  // Panel 1
            Constraint::Percentage(25),  // Panel 2
            Constraint::Percentage(25),  // Panel 3
            Constraint::Percentage(25),  // Panel 4
        ])
        .split(rows[0]);

    // Render the four top panels
    let panel_titles = ["Overdue", "ToDo", "Done", "Deleted"];
    let panel_counts = [
        overdue_count,
        todo_count,
        done_count,
        deleted_count,
    ];

    for (i, panel_area) in top_panels.iter().enumerate() {
        let block = Block::default()
            .title(panel_titles[i])
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::Cyan));

        let inner = block.inner(*panel_area);
        frame.render_widget(block, *panel_area);

        // Determine color based on panel type and value
        let text_style = if i == 0 && panel_counts[i] > 0 {
            // Overdue panel with count > 0: make it red
            Style::default().fg(Color::Red).add_modifier(Modifier::BOLD)
        } else if i == 1 {
            // ToDo panel: make it yellow
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)
        } else {
            // Default: cyan
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
        };

        // Display count as big text
        let big_text = BigText::builder()
            .pixel_size(PixelSize::Full)
            .style(text_style)
            .lines(vec![panel_counts[i].to_string().into()])
            .centered()
            .build();

        // Vertically center the big text using a layout
        let vertical_layout = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Percentage(25),  // Top padding
                Constraint::Percentage(50),  // Content
                Constraint::Percentage(25),  // Bottom padding
            ])
            .split(inner);

        frame.render_widget(big_text, vertical_layout[1]);
    }

    // Render middle row - New Tasks chart
    let middle_block = Block::default()
        .title("New Tasks")
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::Cyan));

    let middle_inner = middle_block.inner(rows[1]);
    frame.render_widget(middle_block, rows[1]);

    // Calculate tasks created per day for the last 3 months
    let today = Local::now().date_naive();
    let three_months_ago = today - Duration::days(90);

    // Create a map of date -> count for tasks created
    let mut task_counts = std::collections::HashMap::new();
    for todo in &all_todos {
        let created_date = todo.created_at.date_naive();
        if created_date >= three_months_ago && created_date <= today {
            *task_counts.entry(created_date).or_insert(0) += 1;
        }
    }

    // Create data points for tasks created (convert to f64 for chart)
    let mut data: Vec<(f64, f64)> = Vec::new();
    for day_offset in 0..=90 {
        let date = three_months_ago + Duration::days(day_offset);
        let count = task_counts.get(&date).copied().unwrap_or(0);
        data.push((day_offset as f64, count as f64));
    }

    // Calculate overdue tasks per day
    let mut overdue_data: Vec<(f64, f64)> = Vec::new();
    for day_offset in 0..=90 {
        let date = three_months_ago + Duration::days(day_offset);
        let overdue_on_this_day = all_todos.iter()
            .filter(|todo| {
                if let Some(due_date) = todo.due_date {
                    // Task is past due on this date
                    let is_past_due = due_date < date;

                    // Task is not completed, or completed after this date
                    let not_completed_yet = if let Some(completed_at) = todo.completed_at {
                        completed_at.date_naive() >= date
                    } else {
                        true
                    };

                    is_past_due && not_completed_yet
                } else {
                    false
                }
            })
            .count();
        overdue_data.push((day_offset as f64, overdue_on_this_day as f64));
    }

    // Calculate tasks completed per day
    let mut completed_counts = std::collections::HashMap::new();
    for todo in &all_todos {
        if let Some(completed_at) = todo.completed_at {
            let completed_date = completed_at.date_naive();
            if completed_date >= three_months_ago && completed_date <= today {
                *completed_counts.entry(completed_date).or_insert(0) += 1;
            }
        }
    }

    // Create data points for tasks completed
    let mut completed_data: Vec<(f64, f64)> = Vec::new();
    for day_offset in 0..=90 {
        let date = three_months_ago + Duration::days(day_offset);
        let count = completed_counts.get(&date).copied().unwrap_or(0);
        completed_data.push((day_offset as f64, count as f64));
    }

    // Create the datasets
    let created_dataset = Dataset::default()
        .name("Tasks Created")
        .marker(symbols::Marker::Braille)
        .graph_type(GraphType::Line)
        .style(Style::default().fg(Color::Yellow))
        .data(&data);

    let overdue_dataset = Dataset::default()
        .name("Overdue Tasks")
        .marker(symbols::Marker::Braille)
        .graph_type(GraphType::Line)
        .style(Style::default().fg(Color::Red))
        .data(&overdue_data);

    let completed_dataset = Dataset::default()
        .name("Tasks Completed")
        .marker(symbols::Marker::Braille)
        .graph_type(GraphType::Line)
        .style(Style::default().fg(Color::Cyan))
        .data(&completed_data);

    // Calculate max y value across all datasets
    let max_y = data.iter()
        .chain(overdue_data.iter())
        .chain(completed_data.iter())
        .map(|(_, y)| *y)
        .fold(0.0, f64::max);

    // Create the chart with all datasets
    let chart = Chart::new(vec![created_dataset, overdue_dataset, completed_dataset])
        .x_axis(
            Axis::default()
                .title("Days ago")
                .style(Style::default().fg(Color::Gray))
                .bounds([0.0, 90.0])
        )
        .y_axis(
            Axis::default()
                .title("Count")
                .style(Style::default().fg(Color::Gray))
                .bounds([0.0, max_y + 1.0])
        );

    frame.render_widget(chart, middle_inner);

    // Render bottom row
    let bottom_block = Block::default()
        .title("Mean time to Done")
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::Cyan));

    let bottom_inner = bottom_block.inner(rows[2]);
    frame.render_widget(bottom_block, rows[2]);

    let bottom_text = Paragraph::new("Bottom content")
        .style(Style::default().fg(Color::Gray))
        .alignment(Alignment::Center);
    frame.render_widget(bottom_text, bottom_inner);
}

fn render_calendar(frame: &mut Frame, app: &App, area: ratatui::layout::Rect) {
    // Create the outer block for the calendar panel
    let calendar_border_style = get_border_style(app.focused_panel == Panel::Calendar);
    let block = Block::default()
        .title("Calendar")
        .borders(Borders::ALL)
        .border_style(calendar_border_style);

    let inner_area = block.inner(area);
    frame.render_widget(block, area);

    // Split the calendar area into three columns for the three months
    let calendar_columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(33),  // Previous month
            Constraint::Percentage(34),  // Current month
            Constraint::Percentage(33),  // Next month
        ])
        .split(inner_area);

    // Convert chrono::NaiveDate to time::Date
    let current_date = chrono_to_time_date(app.current_date);
    let prev_month_date = get_previous_month(app.current_date);
    let next_month_date = get_next_month(app.current_date);

    // Create event store and add all due dates with muted highlight
    let mut events = CalendarEventStore::default();

    let today_naive = Local::now().date_naive();

    // Add all due dates from todos
    for todo in &app.todos {
        if let Some(due_date) = todo.due_date {
            let due_date_time = chrono_to_time_date(due_date);

            // Check if task is overdue (due date is before today and not completed)
            let is_overdue = due_date < today_naive && !todo.completed;

            // Style overdue tasks in red, normal due dates in dark gray
            let style = if is_overdue {
                Style::default().bg(Color::Red).fg(Color::White).add_modifier(Modifier::BOLD)
            } else {
                Style::default().bg(Color::DarkGray).fg(Color::White)
            };

            events.add(due_date_time, style);
        }
    }

    // Add today's date to highlight it (this will override due dates if today has a task)
    let today = chrono_to_time_date(today_naive);
    events.add(today, Style::default().bg(Color::Cyan).fg(Color::Black).add_modifier(Modifier::BOLD));

    // Add selected calendar date (this will override today and due dates when calendar is focused)
    if app.focused_panel == Panel::Calendar {
        if let Some(selected_date) = app.selected_calendar_date {
            let selected_date_time = chrono_to_time_date(selected_date);
            events.add(selected_date_time, Style::default().bg(Color::Yellow).fg(Color::Black).add_modifier(Modifier::BOLD));
        }
    }

    // Create monthly calendar widgets
    let prev_calendar = Monthly::new(chrono_to_time_date(prev_month_date), events.clone())
        .show_month_header(Style::default())
        .show_weekdays_header(Style::default());

    let current_calendar = Monthly::new(current_date, events.clone())
        .show_month_header(Style::default().add_modifier(Modifier::BOLD))
        .show_weekdays_header(Style::default())
        .show_surrounding(Style::default().fg(Color::DarkGray));

    let next_calendar = Monthly::new(chrono_to_time_date(next_month_date), events)
        .show_month_header(Style::default())
        .show_weekdays_header(Style::default());

    // Render the three calendars
    frame.render_widget(prev_calendar, calendar_columns[0]);
    frame.render_widget(current_calendar, calendar_columns[1]);
    frame.render_widget(next_calendar, calendar_columns[2]);
}

fn chrono_to_time_date(date: NaiveDate) -> Date {
    let year = date.year();
    let month = Month::try_from(date.month() as u8).unwrap();
    let day = date.day() as u8;
    Date::from_calendar_date(year, month, day).unwrap()
}

fn get_previous_month(date: NaiveDate) -> NaiveDate {
    let year = date.year();
    let month = date.month();

    if month == 1 {
        NaiveDate::from_ymd_opt(year - 1, 12, 1).unwrap()
    } else {
        NaiveDate::from_ymd_opt(year, month - 1, 1).unwrap()
    }
}

fn get_next_month(date: NaiveDate) -> NaiveDate {
    let year = date.year();
    let month = date.month();

    if month == 12 {
        NaiveDate::from_ymd_opt(year + 1, 1, 1).unwrap()
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1).unwrap()
    }
}

fn render_task_details(frame: &mut Frame, app: &App, area: Rect) {
    let task_border_style = get_border_style(app.focused_panel == Panel::Task);

    // Get the selected task
    let selected_task = app.selected_todo_index
        .and_then(|index| app.todos.get(index));

    if let Some(task) = selected_task {
        // Create the block
        let block = Block::default()
            .title("Task")
            .borders(Borders::ALL)
            .border_style(task_border_style);

        let inner_area = block.inner(area);
        frame.render_widget(block, area);

        // Split the inner area for different fields
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .margin(1)
            .constraints([
                Constraint::Length(3),  // Title
                Constraint::Min(5),     // Description
                Constraint::Length(3),  // Due date
                Constraint::Length(2),  // Created
                Constraint::Length(2),  // Status
            ])
            .split(inner_area);

        // Title
        let title_line = Line::from(vec![
            Span::styled("Title: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(&task.title),
        ]);
        let title_widget = Paragraph::new(title_line);
        frame.render_widget(title_widget, chunks[0]);

        // Description
        let mut description_lines = vec![
            Line::from(Span::styled("Description:", Style::default().add_modifier(Modifier::BOLD))),
        ];
        // Split description by newlines and create a Line for each
        for line in task.description.split('\n') {
            description_lines.push(Line::from(Span::raw(line)));
        }
        let description_widget = Paragraph::new(description_lines)
            .wrap(ratatui::widgets::Wrap { trim: false })
            .scroll((app.task_description_scroll, 0));
        frame.render_widget(description_widget, chunks[1]);

        // Due date
        let due_date_line = if let Some(due_date) = task.due_date {
            Line::from(vec![
                Span::styled("Due Date: ", Style::default().add_modifier(Modifier::BOLD)),
                Span::raw(due_date.format("%Y-%m-%d").to_string()),
            ])
        } else {
            Line::from(vec![
                Span::styled("Due Date: ", Style::default().add_modifier(Modifier::BOLD)),
                Span::raw("Not set"),
            ])
        };
        let due_date_widget = Paragraph::new(due_date_line);
        frame.render_widget(due_date_widget, chunks[2]);

        // Created date
        let created_line = Line::from(vec![
            Span::styled("Created: ", Style::default().fg(Color::Gray).add_modifier(Modifier::BOLD)),
            Span::styled(task.created_at.format("%Y-%m-%d %H:%M").to_string(), Style::default().fg(Color::Gray)),
        ]);
        let created_widget = Paragraph::new(created_line);
        frame.render_widget(created_widget, chunks[3]);

        // Status
        let (status_label_style, status_value_style) = if task.completed {
            (
                Style::default().fg(Color::Green).add_modifier(Modifier::BOLD),
                Style::default().fg(Color::Green)
            )
        } else {
            (
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                Style::default().fg(Color::Yellow)
            )
        };

        let status_line = if task.completed {
            if let Some(completed_at) = task.completed_at {
                Line::from(vec![
                    Span::styled("Status: ", status_label_style),
                    Span::styled(
                        format!("✓ Completed on {}", completed_at.format("%Y-%m-%d %H:%M")),
                        status_value_style
                    ),
                ])
            } else {
                Line::from(vec![
                    Span::styled("Status: ", status_label_style),
                    Span::styled("✓ Completed", status_value_style),
                ])
            }
        } else {
            Line::from(vec![
                Span::styled("Status: ", status_label_style),
                Span::styled("○ Pending", status_value_style),
            ])
        };
        let status_widget = Paragraph::new(status_line);
        frame.render_widget(status_widget, chunks[4]);
    } else {
        // No task selected - show empty panel
        let block = Block::default()
            .title("Task")
            .borders(Borders::ALL)
            .border_style(task_border_style);

        let inner_area = block.inner(area);
        frame.render_widget(block, area);

        let empty_text = Paragraph::new("No task selected")
            .style(Style::default().fg(Color::DarkGray))
            .alignment(Alignment::Center);
        frame.render_widget(empty_text, inner_area);
    }
}

fn render_new_task_panel(frame: &mut Frame, app: &App) {
    // Create a centered rectangle for the popup
    let popup_area = centered_rect(60, 70, frame.area());

    // Clear the area behind the popup
    frame.render_widget(Clear, popup_area);

    // Create the main popup block
    let title = if app.editing_todo_id.is_some() {
        "Edit Task"
    } else {
        "New Task"
    };
    let popup_block = Block::default()
        .title(title)
        .borders(Borders::ALL)
        .style(Style::default().bg(Color::Black));

    // Get the inner area before rendering
    let inner_area = popup_block.inner(popup_area);
    frame.render_widget(popup_block, popup_area);

    // Split the popup into sections
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .margin(1)
        .constraints([
            Constraint::Length(3),  // Title field
            Constraint::Min(10),    // Description field (flexible, at least 10 lines)
            Constraint::Length(3),  // Date field
            Constraint::Length(2),  // Instructions
        ])
        .split(inner_area);

    // Title field
    let title_style = if app.input_mode == InputMode::EditingTitle {
        Style::default().fg(Color::Yellow)
    } else {
        Style::default()
    };

    let title_text = format!("Title: {}", app.new_task_title);
    let title_para = Paragraph::new(title_text)
        .style(title_style);
    frame.render_widget(title_para, chunks[0]);

    // Description field
    let description_style = if app.input_mode == InputMode::EditingDescription {
        Style::default().fg(Color::Yellow)
    } else {
        Style::default()
    };

    let mut description_lines = vec![
        Line::from(Span::styled("Description:", description_style.add_modifier(Modifier::BOLD))),
    ];
    // Split description by newlines and create a Line for each
    for line in app.new_task_description.split('\n') {
        description_lines.push(Line::from(Span::styled(line, description_style)));
    }
    let description_para = Paragraph::new(description_lines)
        .wrap(ratatui::widgets::Wrap { trim: false })
        .scroll((app.edit_description_scroll, 0));
    frame.render_widget(description_para, chunks[1]);

    // Date field
    let date_style = if app.input_mode == InputMode::EditingDate {
        Style::default().fg(Color::Yellow)
    } else {
        Style::default()
    };

    let date_text = if app.input_mode == InputMode::EditingDate {
        format!("Due Date (YYYY-MM-DD): {}", app.date_input_buffer)
    } else {
        format!("Due Date (YYYY-MM-DD): {}",
            app.new_task_due_date
                .map(|d| d.format("%Y-%m-%d").to_string())
                .unwrap_or_else(|| "".to_string()))
    };
    let date_para = Paragraph::new(date_text)
        .style(date_style);
    frame.render_widget(date_para, chunks[2]);

    // Instructions
    let instructions = Paragraph::new(
        "Tab: Switch | Enter: Save | Alt+Enter: New line | Ctrl+U/D or PgUp/Dn: Scroll desc | Esc: Cancel"
    )
    .style(Style::default().fg(Color::Gray))
    .alignment(Alignment::Center);
    frame.render_widget(instructions, chunks[3]);

    // Set cursor position based on which field is being edited
    match app.input_mode {
        InputMode::EditingTitle => {
            let cursor_x = chunks[0].x + 7 + app.new_task_title.len() as u16; // "Title: " is 7 chars
            let cursor_y = chunks[0].y;
            if cursor_x < chunks[0].x + chunks[0].width {
                frame.set_cursor_position((cursor_x, cursor_y));
            }
        }
        InputMode::EditingDescription => {
            // Calculate cursor position for description (accounting for newlines and scroll)
            // Use split('\n') instead of lines() to properly handle trailing newlines
            let lines: Vec<&str> = app.new_task_description.split('\n').collect();
            let line_count = lines.len();
            let last_line = lines.last().map(|s| s.len()).unwrap_or(0);

            let cursor_x = chunks[1].x + last_line as u16;
            // Adjust cursor Y position for scroll offset
            let cursor_y_absolute = chunks[1].y + 1 + (line_count - 1) as u16; // +1 for "Description:" line
            let cursor_y = cursor_y_absolute.saturating_sub(app.edit_description_scroll);

            if cursor_x < chunks[1].x + chunks[1].width && cursor_y >= chunks[1].y && cursor_y < chunks[1].y + chunks[1].height {
                frame.set_cursor_position((cursor_x, cursor_y));
            }
        }
        InputMode::EditingDate => {
            let cursor_x = chunks[2].x + 22 + app.date_input_buffer.len() as u16; // "Due Date (YYYY-MM-DD): " is 22 chars
            let cursor_y = chunks[2].y;
            if cursor_x < chunks[2].x + chunks[2].width {
                frame.set_cursor_position((cursor_x, cursor_y));
            }
        }
        _ => {}
    }
}

fn render_done_panel(frame: &mut Frame, app: &App) {
    // Create a centered rectangle for the popup
    let popup_area = centered_rect(60, 50, frame.area());

    // Clear the area behind the popup
    frame.render_widget(Clear, popup_area);

    // Create the main popup block
    let popup_block = Block::default()
        .title("Done?")
        .borders(Borders::ALL)
        .style(Style::default().bg(Color::Black));

    // Get the inner area before rendering
    let inner_area = popup_block.inner(popup_area);
    frame.render_widget(popup_block, popup_area);

    // Get the task to display
    if let Some(completing_id) = app.completing_todo_id {
        if let Some(task) = app.todos.iter().find(|t| t.id == completing_id) {
            // Split the popup into sections
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .margin(1)
                .constraints([
                    Constraint::Length(3),  // Title field
                    Constraint::Min(5),     // Description field
                    Constraint::Length(3),  // Date field
                    Constraint::Length(3),  // Buttons
                    Constraint::Length(2),  // Instructions
                ])
                .split(inner_area);

            // Title (read-only)
            let title_text = format!("Title: {}", task.title);
            let title_para = Paragraph::new(title_text)
                .style(Style::default().add_modifier(Modifier::BOLD));
            frame.render_widget(title_para, chunks[0]);

            // Description (read-only)
            let mut description_lines = vec![
                Line::from(Span::styled("Description:", Style::default().add_modifier(Modifier::BOLD))),
            ];
            // Split description by newlines and create a Line for each
            for line in task.description.split('\n') {
                description_lines.push(Line::from(line.to_string()));
            }
            let description_para = Paragraph::new(description_lines)
                .wrap(ratatui::widgets::Wrap { trim: false });
            frame.render_widget(description_para, chunks[1]);

            // Due date (read-only)
            let date_text = if let Some(due_date) = task.due_date {
                format!("Due Date: {}", due_date.format("%Y-%m-%d"))
            } else {
                "Due Date: Not set".to_string()
            };
            let date_para = Paragraph::new(date_text);
            frame.render_widget(date_para, chunks[2]);

            // Buttons
            let button_area = chunks[3];
            let button_chunks = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([
                    Constraint::Percentage(50),
                    Constraint::Percentage(50),
                ])
                .split(button_area);

            // Yes button
            let yes_style = if app.done_panel_yes_selected {
                Style::default().bg(Color::Green).fg(Color::Black).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::Green)
            };
            let yes_button = Paragraph::new("[ Yes ]")
                .style(yes_style)
                .alignment(Alignment::Center);
            frame.render_widget(yes_button, button_chunks[0]);

            // No button
            let no_style = if !app.done_panel_yes_selected {
                Style::default().bg(Color::Red).fg(Color::Black).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::Red)
            };
            let no_button = Paragraph::new("[ No ]")
                .style(no_style)
                .alignment(Alignment::Center);
            frame.render_widget(no_button, button_chunks[1]);

            // Instructions
            let instructions = Paragraph::new(
                "Tab/Left/Right: Switch buttons | Enter: Confirm | Esc: Cancel"
            )
            .style(Style::default().fg(Color::Gray))
            .alignment(Alignment::Center);
            frame.render_widget(instructions, chunks[4]);
        }
    }
}

fn render_delete_panel(frame: &mut Frame, app: &App) {
    // Create a centered rectangle for the popup
    let popup_area = centered_rect(60, 50, frame.area());

    // Clear the area behind the popup
    frame.render_widget(Clear, popup_area);

    // Create the main popup block
    let popup_block = Block::default()
        .title("Delete?")
        .borders(Borders::ALL)
        .style(Style::default().bg(Color::Black));

    // Get the inner area before rendering
    let inner_area = popup_block.inner(popup_area);
    frame.render_widget(popup_block, popup_area);

    // Get the task to display
    if let Some(deleting_id) = app.deleting_todo_id {
        if let Some(task) = app.todos.iter().find(|t| t.id == deleting_id) {
            // Split the popup into sections
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .margin(1)
                .constraints([
                    Constraint::Length(3),  // Title field
                    Constraint::Min(3),     // Description field
                    Constraint::Length(3),  // Buttons
                    Constraint::Length(2),  // Instructions
                ])
                .split(inner_area);

            // Title (read-only)
            let title_text = format!("Title: {}", task.title);
            let title_para = Paragraph::new(title_text)
                .style(Style::default().add_modifier(Modifier::BOLD));
            frame.render_widget(title_para, chunks[0]);

            // Description (read-only)
            let mut description_lines = vec![
                Line::from(Span::styled("Description:", Style::default().add_modifier(Modifier::BOLD))),
            ];
            // Split description by newlines and create a Line for each
            for line in task.description.split('\n') {
                description_lines.push(Line::from(line.to_string()));
            }
            let description_para = Paragraph::new(description_lines)
                .wrap(ratatui::widgets::Wrap { trim: false });
            frame.render_widget(description_para, chunks[1]);

            // Buttons
            let button_area = chunks[2];
            let button_chunks = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([
                    Constraint::Percentage(50),
                    Constraint::Percentage(50),
                ])
                .split(button_area);

            // Yes button
            let yes_style = if app.delete_panel_yes_selected {
                Style::default().bg(Color::Green).fg(Color::Black).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::Green)
            };
            let yes_button = Paragraph::new("[ Yes ]")
                .style(yes_style)
                .alignment(Alignment::Center);
            frame.render_widget(yes_button, button_chunks[0]);

            // No button
            let no_style = if !app.delete_panel_yes_selected {
                Style::default().bg(Color::Red).fg(Color::Black).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::Red)
            };
            let no_button = Paragraph::new("[ No ]")
                .style(no_style)
                .alignment(Alignment::Center);
            frame.render_widget(no_button, button_chunks[1]);

            // Instructions
            let instructions = Paragraph::new(
                "Tab/Left/Right: Switch buttons | Enter: Confirm | Esc: Cancel"
            )
            .style(Style::default().fg(Color::Gray))
            .alignment(Alignment::Center);
            frame.render_widget(instructions, chunks[3]);
        }
    }
}

fn render_footer(frame: &mut Frame, area: Rect) {
    let footer_text = Line::from(vec![
        Span::styled(" + ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": new  "),
        Span::styled("d ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": done  "),
        Span::styled("- ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": delete  "),
        Span::styled("tab ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": panels  "),
        Span::styled("t ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": today  "),
        Span::styled("shift+←/→ ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::raw(": tabs"),
    ]);

    let footer = Paragraph::new(footer_text);

    frame.render_widget(footer, area);
}

/// Helper function to create a centered rectangle
fn centered_rect(percent_x: u16, percent_y: u16, r: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(r);

    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}
