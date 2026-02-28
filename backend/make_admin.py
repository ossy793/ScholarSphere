"""
Run this to grant admin access to a user.
Usage (from the backend/ directory):
    python make_admin.py
"""
from app.database import engine
from sqlalchemy import text

email = input("Enter the email address to make admin: ").strip().lower()

with engine.connect() as conn:
    row = conn.execute(
        text("SELECT full_name FROM users WHERE email = :email"),
        {"email": email}
    ).first()

    if not row:
        print(f"\nNo user found with email: {email}")
    else:
        conn.execute(
            text("UPDATE users SET is_admin = TRUE, is_active = TRUE WHERE email = :email"),
            {"email": email}
        )
        conn.commit()
        print(f"\n✓ Done! {row.full_name} ({email}) is now an admin.")
        print("→ Log out of the website and log back in, then the Admin panel will appear.")
