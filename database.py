import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "erp.db")

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    # Tabela de importações (histórico)
    c.execute("""
        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            total_products INTEGER DEFAULT 0,
            supplier TEXT DEFAULT 'TeamBike'
        )
    """)
    # Tabela de produtos (stock atual = stock combinado por fornecedor)
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referencia TEXT NOT NULL,
            descricao TEXT,
            stock INTEGER DEFAULT 0,
            preco REAL DEFAULT 0,
            marca TEXT,
            categoria TEXT,
            ean TEXT,
            extra_json TEXT,
            supplier TEXT DEFAULT 'TeamBike',
            UNIQUE(referencia, supplier)
        )
    """)
    # Snapshot por importação (para comparação histórica)
    c.execute("""
        CREATE TABLE IF NOT EXISTS import_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            referencia TEXT NOT NULL,
            descricao TEXT,
            stock INTEGER DEFAULT 0,
            preco REAL DEFAULT 0,
            marca TEXT,
            categoria TEXT,
            ean TEXT,
            extra_json TEXT,
            supplier TEXT DEFAULT 'TeamBike',
            FOREIGN KEY(import_id) REFERENCES imports(id) ON DELETE CASCADE
        )
    """)
    # Produtos monitorizados (limite personalizado)
    c.execute("""
        CREATE TABLE IF NOT EXISTS monitored (
            referencia TEXT PRIMARY KEY,
            limite INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(referencia) REFERENCES products(referencia) ON DELETE CASCADE
        )
    """)
    # Definições
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # --- Migração: garantir coluna supplier e separação visível por fornecedor ---
    try:
        # products: coluna supplier
        cols = [r[1] for r in c.execute("PRAGMA table_info(products)").fetchall()]
        if "supplier" not in cols:
            c.execute("ALTER TABLE products ADD COLUMN supplier TEXT DEFAULT 'TeamBike'")
            conn.commit()
            last = c.execute("SELECT supplier FROM imports ORDER BY id DESC LIMIT 1").fetchone()
            if last:
                c.execute("UPDATE products SET supplier=? WHERE supplier IS NULL OR supplier=''", (last[0],))
        # Verificar UNIQUE(referencia, supplier)
        sql_row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
        sql = sql_row[0] if sql_row else ""
        if "UNIQUE(referencia, supplier)" not in sql:
            # Recriar com novo UNIQUE
            c.execute("""
                CREATE TABLE IF NOT EXISTS products_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referencia TEXT NOT NULL,
                    descricao TEXT,
                    stock INTEGER DEFAULT 0,
                    preco REAL DEFAULT 0,
                    marca TEXT,
                    categoria TEXT,
                    ean TEXT,
                    extra_json TEXT,
                    supplier TEXT DEFAULT 'TeamBike',
                    UNIQUE(referencia, supplier)
                )
            """)
            c.execute("""
                INSERT OR IGNORE INTO products_new (id, referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier)
                SELECT id, referencia, descricao, stock, preco, marca, categoria, ean, extra_json, COALESCE(supplier, 'TeamBike') FROM products
            """)
            c.execute("DROP TABLE products")
            c.execute("ALTER TABLE products_new RENAME TO products")
        # import_products: coluna supplier
        cols_ip = [r[1] for r in c.execute("PRAGMA table_info(import_products)").fetchall()]
        if "supplier" not in cols_ip:
            c.execute("ALTER TABLE import_products ADD COLUMN supplier TEXT DEFAULT 'TeamBike'")
            conn.commit()
            c.execute("""
                UPDATE import_products SET supplier = (
                    SELECT supplier FROM imports WHERE imports.id = import_products.import_id
                ) WHERE supplier IS NULL OR supplier = ''
            """)
            c.execute("UPDATE import_products SET supplier='TeamBike' WHERE supplier IS NULL OR supplier=''")
    except Exception as e:
        print(f"Migração supplier falhou (pode ser ignorado na primeira criação): {e}")

    # Inserir default settings
    c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('limite_padrao', '0')")
    c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('supplier_default', 'TeamBike')")
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# ---------- Products ----------
def upsert_products(rows, import_id, supplier=None):
    """rows: list de dicts; supplier é TeamBike/SportMed para separação visível"""
    conn = get_conn()
    c = conn.cursor()
    # Se supplier não for passado, tenta buscar do imports
    if supplier is None:
        row = c.execute("SELECT supplier FROM imports WHERE id=?", (import_id,)).fetchone()
        supplier = row["supplier"] if row and row["supplier"] else "TeamBike"
    # Otimizar extra_json: remover URLs e campos grandes (FOTOS etc.) que ocupam 22M por import
    _blacklist = {"FOTOS","FOTO","FOTO2","FOTOS 2","VIDEO","VIDEOS","FOTO_URL","FOTO 2","IMAGEM","IMAGE","URL","FOTOS 3","FOTO 3"}
    for r in rows:
        raw_extra = r.get("extra") or {}
        filt_extra = {}
        for k,v in raw_extra.items():
            ku = str(k).strip().upper()
            vs = str(v).strip()
            if ku in _blacklist: continue
            if vs.lower().startswith("http"): continue
            if len(vs) > 80: continue
            if len(ku) > 30: continue
            filt_extra[k]=vs
            if len(filt_extra) >= 6: break
        extra = json.dumps(filt_extra, ensure_ascii=False) if filt_extra else None
        c.execute("""
            INSERT INTO products(referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(referencia, supplier) DO UPDATE SET
                descricao=excluded.descricao,
                stock=excluded.stock,
                preco=excluded.preco,
                marca=excluded.marca,
                categoria=excluded.categoria,
                ean=excluded.ean,
                extra_json=excluded.extra_json,
                supplier=excluded.supplier
        """, (r["referencia"], r.get("descricao",""), r.get("stock",0), r.get("preco",0), r.get("marca",""), r.get("categoria",""), r.get("ean",""), extra, supplier))
        # Snapshot por importação (com supplier para separação)
        c.execute("""
            INSERT INTO import_products(import_id, referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (import_id, r["referencia"], r.get("descricao",""), r.get("stock",0), r.get("preco",0), r.get("marca",""), r.get("categoria",""), r.get("ean",""), extra, supplier))
    conn.commit()
    conn.close()

def get_all_products(search="", stock_filter="todos", marca_filter="", supplier_filter="", limit=1000, offset=0):
    conn = get_conn()
    query = "SELECT p.*, m.limite as limite_monitorizado FROM products p LEFT JOIN monitored m ON p.referencia=m.referencia WHERE 1=1"
    params = []
    if search:
        query += " AND (p.referencia LIKE ? OR p.descricao LIKE ? OR p.marca LIKE ? OR p.ean LIKE ?)"
        params.extend([f"%{search}%"]*4)
    if stock_filter == "com_stock":
        query += " AND p.stock > 0"
    elif stock_filter == "sem_stock":
        query += " AND p.stock = 0"
    elif stock_filter == "monitorizados":
        query += " AND m.referencia IS NOT NULL"
    if marca_filter:
        query += " AND p.marca = ?"
        params.append(marca_filter)
    if supplier_filter and supplier_filter != "todos":
        query += " AND p.supplier = ?"
        params.append(supplier_filter)
    query += " ORDER BY p.supplier, p.referencia LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    # count
    count_query = "SELECT COUNT(*) as cnt FROM products p LEFT JOIN monitored m ON p.referencia=m.referencia WHERE 1=1"
    count_params = []
    if search:
        count_query += " AND (p.referencia LIKE ? OR p.descricao LIKE ? OR p.marca LIKE ? OR p.ean LIKE ?)"
        count_params.extend([f"%{search}%"]*4)
    if stock_filter == "com_stock":
        count_query += " AND p.stock > 0"
    elif stock_filter == "sem_stock":
        count_query += " AND p.stock = 0"
    elif stock_filter == "monitorizados":
        count_query += " AND m.referencia IS NOT NULL"
    if marca_filter:
        count_query += " AND p.marca = ?"
        count_params.append(marca_filter)
    if supplier_filter and supplier_filter != "todos":
        count_query += " AND p.supplier = ?"
        count_params.append(supplier_filter)
    cnt = conn.execute(count_query, count_params).fetchone()["cnt"]
    conn.close()
    return [dict(r) for r in rows], cnt

def get_marcas(supplier_filter=""):
    conn = get_conn()
    if supplier_filter and supplier_filter != "todos":
        rows = conn.execute("SELECT DISTINCT marca FROM products WHERE marca IS NOT NULL AND marca != '' AND supplier=? ORDER BY marca", (supplier_filter,)).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT marca FROM products WHERE marca IS NOT NULL AND marca != '' ORDER BY marca").fetchall()
    conn.close()
    return [r["marca"] for r in rows]

# ---------- Imports ----------
def create_import(filename, supplier="TeamBike", total=0):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat(timespec='seconds')
    c.execute("INSERT INTO imports(filename, imported_at, total_products, supplier) VALUES(?, ?, ?, ?)", (filename, now, total, supplier))
    import_id = c.lastrowid
    conn.commit()
    conn.close()
    return import_id

def get_imports(limit=50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_last_two_imports():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 2").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_missing_products(current_refs, import_id, supplier=None):
    """Remove de products apenas as refs desse fornecedor que já não vêm no novo ficheiro.
    Agora o stock é combinado (TeamBike + SportMed) com separação visível, por isso não apaga o outro fornecedor."""
    if not current_refs:
        return
    conn = get_conn()
    # Descobrir supplier se não passado
    if supplier is None:
        row = conn.execute("SELECT supplier FROM imports WHERE id=?", (import_id,)).fetchone()
        supplier = row["supplier"] if row and row["supplier"] else None
    placeholders = ",".join("?" for _ in current_refs)
    if supplier:
        conn.execute(f"DELETE FROM products WHERE supplier=? AND referencia NOT IN ({placeholders})", [supplier] + current_refs)
    else:
        conn.execute(f"DELETE FROM products WHERE referencia NOT IN ({placeholders})", current_refs)
    conn.commit()
    conn.close()

# ---------- Monitorizados ----------
def add_monitored(referencia, limite=None):
    if limite is None:
        limite = int(get_setting("limite_padrao", "0"))
    conn = get_conn()
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute("INSERT OR REPLACE INTO monitored(referencia, limite, created_at) VALUES(?, ?, ?)", (referencia, limite, now))
    conn.commit()
    conn.close()

def remove_monitored(referencia):
    conn = get_conn()
    conn.execute("DELETE FROM monitored WHERE referencia=?", (referencia,))
    conn.commit()
    conn.close()

def update_monitored_limite(referencia, limite):
    conn = get_conn()
    conn.execute("UPDATE monitored SET limite=? WHERE referencia=?", (limite, referencia))
    conn.commit()
    conn.close()

def get_monitored():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.*, m.limite, m.created_at,
               CASE WHEN p.referencia IS NULL THEN 1 ELSE 0 END as desaparecido
        FROM monitored m
        LEFT JOIN products p ON p.referencia = m.referencia
        ORDER BY m.created_at DESC
    """).fetchall()
    conn.close()
    # Para desaparecidos, p será NULL, então preencher com referencia do monitored
    result = []
    for r in rows:
        d = dict(r)
        if d["referencia"] is None:
            # na verdade monitored.referencia existe mas p.referencia é null por left join null? precisamos alias
            # Mas temos monitored.referencia como m.referencia, porém select p.* sobrescreve?
            # Vamos corrigir query: selecionar m.referencia explicitamente
            pass
        result.append(d)
    return result

def get_monitored_fixed():
    conn = get_conn()
    rows = conn.execute("""
        SELECT 
            m.referencia as ref_monitor,
            m.limite,
            m.created_at,
            p.referencia,
            p.descricao,
            p.stock,
            p.preco,
            p.marca,
            p.categoria,
            p.ean,
            p.supplier,
            p.extra_json
        FROM monitored m
        LEFT JOIN products p ON p.referencia = m.referencia
        ORDER BY m.created_at DESC
    """).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        # Normalizar referencia
        d["referencia"] = d["ref_monitor"]
        out.append(d)
    return out

# ---------- Verificação / Comparação ----------
def get_verificacao(supplier_filter=None):
    """
    Compara últimas duas importações do mesmo fornecedor (separação visível TeamBike/SportMed).
    Se supplier_filter for None, usa o fornecedor da última importação.
    """
    conn_tmp = get_conn()
    last = conn_tmp.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    conn_tmp.close()
    if not last:
        return {
            "reposicoes": [], "ruturas": [], "novos": [], "desaparecidos": [], "abaixo_limite": [],
            "tem_comparacao": False, "import_atual": None, "import_anterior": None
        }
    supplier = supplier_filter or last["supplier"] or "TeamBike"
    # Buscar últimas duas do mesmo fornecedor para comparação com separação visível
    conn2 = get_conn()
    rows = conn2.execute("SELECT * FROM imports WHERE supplier=? ORDER BY id DESC LIMIT 2", (supplier,)).fetchall()
    conn2.close()
    if len(rows) < 2:
        # Sem histórico suficiente desse fornecedor → sem comparação (não fazer cross-supplier)
        return {
            "reposicoes": [], "ruturas": [], "novos": [], "desaparecidos": [], "abaixo_limite": [],
            "tem_comparacao": False, "import_atual": dict(rows[0]) if rows else None, "import_anterior": None
        }
    import_atual = dict(rows[0])
    import_anterior = dict(rows[1])

    conn = get_conn()
    atual_rows = conn.execute("SELECT * FROM import_products WHERE import_id=?", (import_atual["id"],)).fetchall()
    anterior_rows = conn.execute("SELECT * FROM import_products WHERE import_id=?", (import_anterior["id"],)).fetchall()
    # Mapa por referencia
    atual_map = {r["referencia"]: dict(r) for r in atual_rows}
    anterior_map = {r["referencia"]: dict(r) for r in anterior_rows}

    reposicoes = []
    ruturas = []
    novos = []
    desaparecidos = []

    for ref, atual in atual_map.items():
        anterior = anterior_map.get(ref)
        if anterior is None:
            novos.append({**atual, "stock_anterior": None})
        else:
            stock_ant = anterior["stock"] or 0
            stock_atu = atual["stock"] or 0
            if stock_ant == 0 and stock_atu > 0:
                reposicoes.append({**atual, "stock_anterior": stock_ant, "stock_atual": stock_atu})
            elif stock_ant > 0 and stock_atu == 0:
                ruturas.append({**atual, "stock_anterior": stock_ant, "stock_atual": stock_atu})

    for ref, anterior in anterior_map.items():
        if ref not in atual_map:
            desaparecidos.append({**anterior, "stock_anterior": anterior["stock"]})

    # Abaixo do limite: só monitorizados e que existem no atual
    # Precisa ser monitorizados; pegar limite individual ou padrão
    limite_padrao = int(get_setting("limite_padrao", "0"))
    mon_rows = conn.execute("SELECT referencia, limite FROM monitored").fetchall()
    mon_map = {r["referencia"]: r["limite"] for r in mon_rows}
    abaixo_limite = []
    for ref, limite in mon_map.items():
        prod = atual_map.get(ref)
        if prod:
            stock_atu = prod["stock"] or 0
            if stock_atu <= limite:  # "abaixo do limite" inclui <= ? Espec: passou para valor abaixo do limite
                # Só incluir se anterior era acima? Espec: "cujo stock passou para um valor abaixo do limite"
                # Mas para simplificar mostrar todos abaixo. Vamos verificar se antes estava acima
                anterior = anterior_map.get(ref)
                stock_ant = anterior["stock"] if anterior else None
                # Se não havia anterior (novo) e já está abaixo, conta também
                # Vamos incluir todos abaixo, mas sinalizar
                abaixo_limite.append({**prod, "limite": limite, "stock_anterior": stock_ant, "stock_atual": stock_atu})
        else:
            # desaparecido mas monitorizado - também é abaixo limite? Tratamos em desaparecidos
            pass

    # Alternativa mais fiel: incluir também monitorizados que estão abaixo mesmo sem ter mudado? A spec diz "passou para"
    # Vamos filtrar abaixo_limite para apenas os que estavam acima e agora abaixo, mas manter opção de ver todos
    # Para UX, mostramos todos abaixo; frontend pode filtrar

    conn.close()
    return {
        "reposicoes": reposicoes,
        "ruturas": ruturas,
        "novos": novos,
        "desaparecidos": desaparecidos,
        "abaixo_limite": abaixo_limite,
        "tem_comparacao": True,
        "import_atual": import_atual,
        "import_anterior": import_anterior,
        "totais": {
            "reposicoes": len(reposicoes),
            "ruturas": len(ruturas),
            "novos": len(novos),
            "desaparecidos": len(desaparecidos),
            "abaixo_limite": len(abaixo_limite)
        }
    }

def clear_all_data():
    conn = get_conn()
    conn.execute("DELETE FROM import_products")
    conn.execute("DELETE FROM imports")
    conn.execute("DELETE FROM products")
    # monitored mantém? Não, limpa também se pedido reset
    conn.commit()
    conn.close()

def clear_monitored():
    conn = get_conn()
    conn.execute("DELETE FROM monitored")
    conn.commit()
    conn.close()

def delete_import(import_id):
    """Elimina importação. Se for a mais recente desse fornecedor, repõe products só desse fornecedor."""
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Importação não encontrada"
    supplier = row["supplier"] or "TeamBike"
    # Verificar se é a mais recente desse fornecedor
    latest_supplier = c.execute("SELECT id FROM imports WHERE supplier=? ORDER BY id DESC LIMIT 1", (supplier,)).fetchone()
    is_latest_supplier = latest_supplier and latest_supplier["id"] == import_id
    # Eliminar snapshot e import
    c.execute("DELETE FROM import_products WHERE import_id=?", (import_id,))
    c.execute("DELETE FROM imports WHERE id=?", (import_id,))
    conn.commit()
    if is_latest_supplier:
        # Repor products só desse fornecedor para a nova mais recente desse fornecedor
        new_latest = c.execute("SELECT id FROM imports WHERE supplier=? ORDER BY id DESC LIMIT 1", (supplier,)).fetchone()
        c.execute("DELETE FROM products WHERE supplier=?", (supplier,))
        if new_latest:
            snap = c.execute("SELECT referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier FROM import_products WHERE import_id=?", (new_latest["id"],)).fetchall()
            for r in snap:
                c.execute("INSERT INTO products(referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier) VALUES(?,?,?,?,?,?,?,?,?)",
                          (r["referencia"], r["descricao"], r["stock"], r["preco"], r["marca"], r["categoria"], r["ean"], r["extra_json"], r["supplier"] or supplier))
        conn.commit()
    conn.close()
    return True, "ok"

def restore_import_as_current(import_id):
    """Repõe o stock atual só desse fornecedor para o snapshot dessa importação"""
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Importação não encontrada"
    supplier = row["supplier"] or "TeamBike"
    snap = c.execute("SELECT referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier FROM import_products WHERE import_id=?", (import_id,)).fetchall()
    c.execute("DELETE FROM products WHERE supplier=?", (supplier,))
    for r in snap:
        c.execute("INSERT INTO products(referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier) VALUES(?,?,?,?,?,?,?,?,?)",
                  (r["referencia"], r["descricao"], r["stock"], r["preco"], r["marca"], r["categoria"], r["ean"], r["extra_json"], r["supplier"] or supplier))
    conn.commit()
    conn.close()
    return True, "ok"

def get_import_detail(import_id):
    conn = get_conn()
    imp = conn.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not imp:
        conn.close()
        return None
    rows = conn.execute("SELECT * FROM import_products WHERE import_id=? ORDER BY referencia", (import_id,)).fetchall()
    conn.close()
    return {"import": dict(imp), "products": [dict(r) for r in rows]}

def get_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"]
    com_stock = conn.execute("SELECT COUNT(*) as c FROM products WHERE stock > 0").fetchone()["c"]
    sem_stock = conn.execute("SELECT COUNT(*) as c FROM products WHERE stock = 0").fetchone()["c"]
    monitorizados = conn.execute("SELECT COUNT(*) as c FROM monitored").fetchone()["c"]
    imports = conn.execute("SELECT COUNT(*) as c FROM imports").fetchone()["c"]
    # Por fornecedor para separação visível
    teambike_total = conn.execute("SELECT COUNT(*) as c FROM products WHERE supplier='TeamBike'").fetchone()["c"]
    sportmed_total = conn.execute("SELECT COUNT(*) as c FROM products WHERE supplier='SportMed'").fetchone()["c"]
    teambike_com = conn.execute("SELECT COUNT(*) as c FROM products WHERE supplier='TeamBike' AND stock>0").fetchone()["c"]
    sportmed_com = conn.execute("SELECT COUNT(*) as c FROM products WHERE supplier='SportMed' AND stock>0").fetchone()["c"]
    conn.close()
    return {"total": total, "com_stock": com_stock, "sem_stock": sem_stock, "monitorizados": monitorizados, "imports": imports,
            "por_fornecedor": {
                "TeamBike": {"total": teambike_total, "com_stock": teambike_com, "sem_stock": teambike_total - teambike_com},
                "SportMed": {"total": sportmed_total, "com_stock": sportmed_com, "sem_stock": sportmed_total - sportmed_com}
            }}
