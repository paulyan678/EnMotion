fn main() {
    println!("cargo:rerun-if-env-changed=ENMOTION_CONTROL_PLANE_URL");
    tauri_build::build();
}
