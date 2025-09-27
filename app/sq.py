import sqlite3

DB_FILE = "ledger.db"

def view_invoices():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM receipts")
    rows = c.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    invoices = view_invoices()
    if not invoices:
        print("No receipts found in ledger.db")
    else:
        for row in invoices:
            print(row)
