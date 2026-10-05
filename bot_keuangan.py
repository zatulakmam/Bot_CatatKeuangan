"""
Bot Telegram pencatat pemasukan & pengeluaran.

Setup:
  pip install "python-telegram-bot>=20"
  export BOT_TOKEN="token_dari_BotFather"
  python bot_keuangan.py
"""
import os
import re
import sqlite3
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = "keuangan.db"

BANTUAN = (
    "Catat keuangan kamu 💰\n\n"
    "Pemasukan:\n/masuk 5jt gaji   atau   +5jt gaji\n\n"
    "Pengeluaran:\n/keluar 25rb makan siang   atau   -25rb makan siang\n\n"
    "Lainnya:\n"
    "/saldo - saldo total\n"
    "/laporan - ringkasan bulan ini\n"
    "/riwayat - 10 transaksi terakhir\n"
    "/hapus <id> - hapus transaksi\n\n"
    "Format jumlah: 25000, 25.000, 25rb, 25k, 1.5jt"
)


def koneksi():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            jenis TEXT NOT NULL CHECK (jenis IN ('masuk', 'keluar')),
            jumlah INTEGER NOT NULL,
            catatan TEXT,
            waktu TEXT NOT NULL
        )"""
    )
    return conn


def rupiah(n: int) -> str:
    return "Rp" + f"{n:,}".replace(",", ".")


def parse_jumlah(teks: str):
    teks = teks.lower().replace("rp", "").strip()
    m = re.fullmatch(r"([\d.,]+)\s*(k|rb|ribu|jt|juta)?", teks)
    if not m:
        return None
    angka, satuan = m.groups()
    kali = {None: 1, "k": 1_000, "rb": 1_000, "ribu": 1_000,
            "jt": 1_000_000, "juta": 1_000_000}[satuan]
    try:
        if satuan:  # "1.5jt" / "1,5jt" -> desimal
            n = float(angka.replace(",", "."))
        else:       # "25.000" -> pemisah ribuan
            n = float(angka.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    n = int(round(n * kali))
    return n if n > 0 else None


async def simpan(update: Update, jenis: str, jumlah_str: str, catatan: str):
    jumlah = parse_jumlah(jumlah_str)
    if jumlah is None:
        await update.message.reply_text(
            "Jumlah tidak valid. Contoh: /keluar 25rb makan siang")
        return
    with koneksi() as conn:
        cur = conn.execute(
            "INSERT INTO transaksi (user_id, jenis, jumlah, catatan, waktu) "
            "VALUES (?, ?, ?, ?, ?)",
            (update.effective_user.id, jenis, jumlah, catatan.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        tid = cur.lastrowid
    ikon = "🟢 Pemasukan" if jenis == "masuk" else "🔴 Pengeluaran"
    await update.message.reply_text(
        f"{ikon} tercatat (#{tid})\n{rupiah(jumlah)}"
        + (f" — {catatan.strip()}" if catatan.strip() else ""))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(BANTUAN)


async def cmd_masuk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: /masuk 5jt gaji")
        return
    await simpan(update, "masuk", context.args[0], " ".join(context.args[1:]))


async def cmd_keluar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: /keluar 25rb makan siang")
        return
    await simpan(update, "keluar", context.args[0], " ".join(context.args[1:]))


async def teks_cepat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tangani pesan seperti '+5jt gaji' atau '-25rb makan'."""
    m = re.match(r"^([+-])\s*(\S+)\s*(.*)$", update.message.text.strip())
    if not m:
        return
    tanda, jumlah, catatan = m.groups()
    await simpan(update, "masuk" if tanda == "+" else "keluar", jumlah, catatan)


def total(conn, user_id, awal=None):
    q = "SELECT jenis, COALESCE(SUM(jumlah), 0) FROM transaksi WHERE user_id = ?"
    params = [user_id]
    if awal:
        q += " AND waktu >= ?"
        params.append(awal)
    q += " GROUP BY jenis"
    hasil = dict(conn.execute(q, params).fetchall())
    return hasil.get("masuk", 0), hasil.get("keluar", 0)


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with koneksi() as conn:
        masuk, keluar = total(conn, update.effective_user.id)
    await update.message.reply_text(
        f"💼 Saldo: {rupiah(masuk - keluar)}\n"
        f"🟢 Total masuk: {rupiah(masuk)}\n"
        f"🔴 Total keluar: {rupiah(keluar)}")


async def laporan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sekarang = datetime.now()
    awal = sekarang.replace(day=1, hour=0, minute=0, second=0,
                            microsecond=0).isoformat(timespec="seconds")
    with koneksi() as conn:
        masuk, keluar = total(conn, update.effective_user.id, awal)
        terbesar = conn.execute(
            "SELECT jumlah, catatan FROM transaksi WHERE user_id = ? "
            "AND jenis = 'keluar' AND waktu >= ? ORDER BY jumlah DESC LIMIT 3",
            (update.effective_user.id, awal)).fetchall()
    teks = (f"📊 Laporan {sekarang.strftime('%m/%Y')}\n"
            f"🟢 Masuk: {rupiah(masuk)}\n"
            f"🔴 Keluar: {rupiah(keluar)}\n"
            f"💼 Selisih: {rupiah(masuk - keluar)}")
    if terbesar:
        teks += "\n\nPengeluaran terbesar:\n" + "\n".join(
            f"• {rupiah(j)} {c or '-'}" for j, c in terbesar)
    await update.message.reply_text(teks)


async def riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with koneksi() as conn:
        baris = conn.execute(
            "SELECT id, jenis, jumlah, catatan, waktu FROM transaksi "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (update.effective_user.id,)).fetchall()
    if not baris:
        await update.message.reply_text("Belum ada transaksi.")
        return
    teks = "\n".join(
        f"#{i} {'🟢' if j == 'masuk' else '🔴'} {rupiah(n)} "
        f"{c or ''} ({w[5:10]})" for i, j, n, c, w in baris)
    await update.message.reply_text(teks)


async def hapus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Contoh: /hapus 12")
        return
    with koneksi() as conn:
        cur = conn.execute(
            "DELETE FROM transaksi WHERE id = ? AND user_id = ?",
            (int(context.args[0]), update.effective_user.id))
    await update.message.reply_text(
        "Transaksi dihapus ✅" if cur.rowcount else "ID tidak ditemukan.")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help", "bantuan"], start))
    app.add_handler(CommandHandler("masuk", cmd_masuk))
    app.add_handler(CommandHandler("keluar", cmd_keluar))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, teks_cepat))
    app.run_polling()


if __name__ == "__main__":
    main()