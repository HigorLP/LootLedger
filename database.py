import sqlite3
import os

# Cria um caminho absoluto inquebrável para o banco de dados
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'inventario_cs2.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL)''')

    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome_item TEXT NOT NULL, categoria TEXT, 
        plataforma TEXT, preco_compra REAL, data_compra TEXT, status TEXT DEFAULT 'Ativo',
        preco_venda REAL DEFAULT 0, data_venda TEXT, plataforma_venda TEXT,
        in_container INTEGER DEFAULT 0, preco_mercado REAL DEFAULT 0, 
        preco_alvo REAL DEFAULT 0, user_id INTEGER, steam_asset_id TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS plataformas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, taxa REAL DEFAULT 0, mostrar_tesouraria INTEGER DEFAULT 1, user_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, user_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sites_bets (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, user_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tesouraria (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL, valor REAL, user_id INTEGER)''')

    conn.commit()
    conn.close()
    print("✅ Estrutura do Banco de Dados LootLedger inicializada (Caminho Absoluto)!")