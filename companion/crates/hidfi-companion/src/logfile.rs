//! A log file in the app folder (`companion.log`), capped at 1 MB with one
//! previous file kept, so a machine running for months never fills its disk
//! and a support question can be answered from the last few days.
//! No secret is ever logged: keys, tickets and codes are not passed to `log`.

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

const CAP: u64 = 1024 * 1024;

pub struct FileLog {
    path: PathBuf,
    file: Mutex<Option<File>>,
    level: log::LevelFilter,
}

impl FileLog {
    pub fn install(dir: &std::path::Path, level: log::LevelFilter) {
        let path = dir.join("companion.log");
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .ok();
        let me = Box::leak(Box::new(FileLog {
            path,
            file: Mutex::new(file),
            level,
        }));
        if log::set_logger(me).is_ok() {
            log::set_max_level(level);
        }
    }

    fn rotate(&self, f: &mut Option<File>) {
        if f.as_ref()
            .and_then(|f| f.metadata().ok())
            .is_some_and(|m| m.len() > CAP)
        {
            *f = None;
            let _ = fs::rename(&self.path, self.path.with_extension("log.1"));
            *f = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)
                .ok();
        }
    }
}

impl log::Log for FileLog {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= self.level
            && !m.target().starts_with("str0m")
            && !m.target().starts_with("dimpl")
    }
    fn log(&self, r: &log::Record) {
        if !self.enabled(r.metadata()) {
            return;
        }
        let Ok(mut g) = self.file.lock() else { return };
        self.rotate(&mut g);
        if let Some(f) = g.as_mut() {
            let t = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(f, "{t} {:<5} {}: {}", r.level(), r.target(), r.args());
        }
    }
    fn flush(&self) {
        if let Ok(mut g) = self.file.lock() {
            if let Some(f) = g.as_mut() {
                let _ = f.flush();
            }
        }
    }
}
