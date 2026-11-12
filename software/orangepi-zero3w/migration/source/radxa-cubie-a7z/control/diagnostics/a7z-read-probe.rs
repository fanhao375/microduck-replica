//! Diagnostic only: the same read-only Bus as robotd, no policy or write APIs.
use duck_control::{feetech::Bus, model::JOINT_IDS};
use serde_json::json;
use std::{
    path::Path,
    time::{Duration, Instant},
};
unsafe extern "C" {
    fn fcntl(fd: i32, cmd: i32, ...) -> i32;
}
fn main() {
    let a: Vec<String> = std::env::args().collect();
    assert_eq!(a.len(), 4);
    let cycles: u32 = a[2].parse().unwrap();
    assert!((1..=3000).contains(&cycles));
    let pid = std::process::Command::new("systemctl")
        .args(["show", "robotd", "--property=MainPID", "--value"])
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8(pid.stdout).unwrap().trim(),
        "0",
        "robotd must be stopped for the library exclusive lock"
    );
    assert_eq!(a[1], "stopped");
    assert_eq!(std::env::var("CUBIE_A7Z_SYNC_GROUP_SIZE").unwrap(), "3");
    let mut bus = Bus::open_read_only("/dev/ttyS2").unwrap();
    if a[3] == "nonblocking" {
        let tty: Vec<_> = std::fs::read_dir("/proc/self/fd")
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| {
                std::fs::read_link(e.path()).ok().as_deref() == Some(Path::new("/dev/ttyAS0"))
            })
            .collect();
        assert_eq!(tty.len(), 1);
        let fd: i32 = tty[0].file_name().to_string_lossy().parse().unwrap();
        let flags = unsafe { fcntl(fd, 3) };
        assert!(flags >= 0);
        assert_eq!(unsafe { fcntl(fd, 4, flags | 0x800) }, 0);
    } else {
        assert_eq!(a[3], "blocking");
    }
    bus.identify_imu().unwrap();
    let period = Duration::from_millis(20);
    let start = Instant::now();
    let mut next = start;
    let (mut good, mut failed, mut worst) = (0u32, 0u32, 0u64);
    let mut examples = Vec::new();
    for cycle in 0..cycles {
        let got = bus.telemetry(&JOINT_IDS);
        worst = worst.max(bus.last_sync.elapsed_us);
        match got {
            Ok(rows) => {
                assert_eq!(rows.len(), 16);
                good += 1;
            }
            Err(e) => {
                failed += 1;
                if examples.len() < 5 {
                    examples
                        .push(json!({"cycle":cycle,"error":e.to_string(),"trace":bus.last_sync}));
                }
            }
        }
        next += period;
        let now = Instant::now();
        while next < now {
            next += period;
        }
        std::thread::sleep(next.saturating_duration_since(Instant::now()));
    }
    println!(
        "{}",
        json!({"mode":a[3],"cycles":cycles,"success":good,"failures":failed,"hz":cycles as f64/start.elapsed().as_secs_f64(),"worst_us":worst,"first_failures":examples,"motor_writes_blocked":true})
    );
}
