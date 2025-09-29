#!/usr/bin/env python3
"""
Migration script to convert old JSON config files to the new SQLite database system
"""
import json
import os
import sys
from database import db

def migrate_config():
    """Migrate from old JSON config files to new database system"""
    
    # Migrate ReportToBan config if it exists
    reporttoban_config_path = 'config.reporttoban.json'
    if os.path.exists(reporttoban_config_path):
        print(f"[+] Found {reporttoban_config_path}, migrating to global config...")
        try:
            with open(reporttoban_config_path, 'r') as f:
                old_config = json.load(f)
            
            # Migrate relevant settings to global config (except TOKEN which is in config.json)
            settings_to_migrate = [
                "REPORT_CHANNEL_ID",
                "MODERATION_MESSAGE_CHANNEL_ID", 
                "REPORTED_MESSAGE",
                "REPORT_BLACKLIST",
                "REPORT_RATE_LIMIT",
                "REPORT_MESSAGE"
            ]
            
            for key in settings_to_migrate:
                if key in old_config:
                    db.set_global_config(key, old_config[key])
                    print(f"   Migrated {key}: {old_config[key]}")
            
            # Backup the old file
            backup_path = reporttoban_config_path + '.backup'
            os.rename(reporttoban_config_path, backup_path)
            print(f"[+] Original config backed up to {backup_path}")
            
        except Exception as e:
            print(f"[!] Error migrating {reporttoban_config_path}: {e}")
    
    # Check main config.json for TOKEN
    main_config_path = 'config.json'
    if os.path.exists(main_config_path):
        print(f"[+] {main_config_path} will continue to be used for TOKEN")
    else:
        print(f"[!] {main_config_path} not found. Please ensure TOKEN is set in this file.")
    
    print(f"[+] Migration complete! Database created at: {db.db_path}")
    print(f"[+] Use the /設定 command in Discord to configure per-server settings.")

if __name__ == "__main__":
    print("=== Config Migration Tool ===")
    print("This will migrate your old JSON config files to the new SQLite database system.")
    
    if input("Continue? (y/N): ").lower().strip() != 'y':
        print("Migration cancelled.")
        sys.exit(0)
    
    migrate_config()