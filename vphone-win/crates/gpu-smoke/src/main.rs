//! `cargo run -p reims-vgpu-vulkan --example windows_gpu_smoke`: the Windows platform's views of
//! guest pages imported into the host GPU by pointer, written by the GPU (os/windows/smoke.rs).

#[cfg(windows)]
fn main() {
    match reims_vgpu_vulkan::os::gpu_import_smoke() {
        Ok(lines) => {
            for line in lines {
                println!("{line}");
            }
            println!("SMOKE OK");
        }
        Err(why) => {
            println!("SMOKE FAILED: {why}");
            std::process::exit(1);
        }
    }
}

#[cfg(not(windows))]
fn main() {
    println!("Windows only");
}
