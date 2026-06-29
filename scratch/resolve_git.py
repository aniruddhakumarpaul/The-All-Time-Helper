import os
import shutil
import subprocess
import sys
from datetime import datetime

def run_cmd(args):
    print(f"Running: {' '.join(args)}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error executing command: {' '.join(args)}")
        print(f"Stdout:\n{result.stdout}")
        print(f"Stderr:\n{result.stderr}")
        return False, result.stdout, result.stderr
    return True, result.stdout, result.stderr

def should_backup(filepath):
    norm_path = filepath.replace('\\', '/')
    parts = norm_path.split('/')
    
    # Directories to ignore entirely
    ignore_names = {
        '.venv', '.pycache_runtime', '.codex', '.sixth', '.git', 
        '.git_local_backups', 'codex_hook_state', '__pycache__', 
        '.pytest_cache', 'logs', 'node_modules', '.vscode'
    }
    
    # Specific files to ignore
    ignore_files = {
        'tmp_startup_probe.err', 'tmp_startup_probe.out',
        'resolve_git.py'
    }
    
    for part in parts:
        if part in ignore_names:
            return False
            
    if parts[-1] in ignore_files or filepath.endswith('resolve_git.py'):
        return False
        
    return True

def get_git_files():
    # Get modified tracked files
    success, stdout, _ = run_cmd(["git", "status", "--porcelain"])
    if not success:
        return [], []
        
    modified_files = []
    untracked_files = []
    
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        
        status = line[:2]
        filepath = line[3:].strip('"')
        
        # Check if we should ignore this file/folder
        if not should_backup(filepath):
            continue
            
        # '??' status means untracked
        if status == '??':
            untracked_files.append(filepath)
        else:
            modified_files.append(filepath)
            
    return modified_files, untracked_files

def backup_files(files, backup_dir):
    print(f"Backing up files to {backup_dir}...")
    for filepath in files:
        if not os.path.exists(filepath):
            continue
            
        dest_path = os.path.join(backup_dir, filepath)
        try:
            if os.path.isdir(filepath):
                shutil.copytree(filepath, dest_path, dirs_exist_ok=True)
            else:
                dest_dir = os.path.dirname(dest_path)
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(filepath, dest_path)
            print(f"  Backed up: {filepath}")
        except Exception as e:
            print(f"  Warning: Could not back up {filepath}. Error: {e}")

def main():
    print("--- Starting Git Hard Reset and Update (Safeguarded) ---")
    
    # 1. Identify files to back up
    modified_files, untracked_files = get_git_files()
    all_files = modified_files + untracked_files
    
    if not all_files:
        print("No local modifications or untracked files detected.")
    else:
        # 2. Create backup directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(".git_local_backups", f"backup_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)
        
        # 3. Perform backup
        backup_files(all_files, backup_dir)
        print(f"All local files backed up successfully to: {backup_dir}")
        
    # 4. Fetch the latest from remote
    success, _, _ = run_cmd(["git", "fetch", "origin"])
    if not success:
        print("Failed to fetch from remote origin. Aborting reset.")
        sys.exit(1)
        
    # 5. Hard reset to remote main
    print("Performing hard reset to origin/main...")
    success, _, _ = run_cmd(["git", "reset", "--hard", "origin/main"])
    if not success:
        print("Failed to reset hard to origin/main.")
        sys.exit(1)
        
    # 6. Clean untracked files (excluding the backup directory and the script itself)
    print("Cleaning untracked files...")
    success, _, _ = run_cmd(["git", "clean", "-fd", "-e", ".git_local_backups", "-e", "scratch/resolve_git.py"])
    if not success:
        print("Failed to run git clean.")
        sys.exit(1)
        
    print("\n--- Success! Local repository is now updated and matches remote origin/main. ---")
    if all_files:
        print(f"Note: Your local changes are preserved in the backup folder: {backup_dir}")

if __name__ == "__main__":
    main()
