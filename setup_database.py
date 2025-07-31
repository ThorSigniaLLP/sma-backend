#!/usr/bin/env python3
"""
Database Setup Script for Automation Dashboard

This script helps you set up your database properly using Alembic migrations.
Run this script when you first set up the project or when you need to reset your database.
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"\n🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"Error: {e.stderr}")
        return False

def check_alembic_installed():
    """Check if alembic is available"""
    try:
        subprocess.run([sys.executable, "-m", "alembic", "--version"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def check_database_state():
    """Check if database has tables but no alembic_version"""
    try:
        # Try to get current version
        result = subprocess.run(["python", "-m", "alembic", "current"], 
                              capture_output=True, text=True)
        
        if "No such table 'alembic_version'" in result.stderr:
            return "no_alembic_version"
        elif "Target database is not up to date" in result.stderr:
            return "outdated"
        else:
            return "up_to_date"
    except Exception:
        return "unknown"

def main():
    print("🚀 Database Setup for Automation Dashboard")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not Path("alembic.ini").exists():
        print("❌ Error: alembic.ini not found. Please run this script from the backend directory.")
        sys.exit(1)
    
    # Check if alembic is installed
    if not check_alembic_installed():
        print("❌ Error: Alembic is not installed. Please install it with:")
        print("   pip install alembic")
        sys.exit(1)
    
    print("✅ Alembic is available")
    
    # Step 1: Check current database state
    print("\n📊 Checking current database state...")
    db_state = check_database_state()
    
    if db_state == "no_alembic_version":
        print("ℹ️  Database has tables but Alembic doesn't know about them.")
        print("   This happens when you used create_tables() before setting up Alembic.")
        print("   Marking current state as up-to-date...")
        
        if not run_command("python -m alembic stamp head", "Marking current state as up-to-date"):
            print("❌ Failed to stamp database. Exiting.")
            sys.exit(1)
            
    elif db_state == "outdated":
        print("ℹ️  Database is outdated. Applying pending migrations...")
        
    elif db_state == "up_to_date":
        print("ℹ️  Database is already up-to-date!")
        print("\n🎉 No action needed!")
        return
        
    else:
        print("ℹ️  Database state unknown. Attempting to apply migrations...")
    
    # Step 2: Apply any pending migrations
    if db_state != "up_to_date":
        if not run_command("python -m alembic upgrade head", "Applying all migrations"):
            print("❌ Failed to apply migrations. Exiting.")
            sys.exit(1)
    
    # Step 3: Verify setup
    print("\n🔍 Verifying database setup...")
    if not run_command("python -m alembic current", "Checking final database state"):
        print("❌ Failed to verify database state.")
        sys.exit(1)
    
    print("\n🎉 Database setup completed successfully!")
    print("\n📋 Next steps:")
    print("1. Start your application: uvicorn app.main:app --reload")
    print("2. For future schema changes:")
    print("   - Modify your models")
    print("   - Run: python -m alembic revision --autogenerate -m 'description'")
    print("   - Run: python -m alembic upgrade head")
    print("\n📖 For more information, see: ALEMBIC_GUIDE.md")

if __name__ == "__main__":
    main() 