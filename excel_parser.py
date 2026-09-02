import pandas as pd
import re

# Mapeamento inteligente de colunas
# Detecta automaticamente baseado em nomes comuns PT/EN

COLUMN_ALIASES = {
    "referencia": ["referencia", "referência", "código modelo", "codigo modelo", "ref", "codigo", "código", "sku", "artigo", "cod", "cod. artigo", "cod artigo", "id", "item", "product code"],
    "descricao": ["nombre producto", "nome produto", "descripcion", "descripción", "descricao", "descrição", "designacao", "designação", "description", "nome", "nombre", "artigo", "producto", "produto", "desc", "denominacao", "denominação", "label"],
    "stock": ["stock", "disponible", "disponibilidad", "disponivel", "disponível", "qtd", "quantidade", "qty", "quantity", "existencias", "existências", "saldo", "quant", "unidades", "disponibilidade", "stk"],
    "preco": ["pvpr", "coste tarifa", "coste", "tarifa", "pvp", "preco", "preço", "precio", "price", "custo", "preço venda", "preco venda", "precio venta", "valor", "pvp c/ iva", "pvp s/ iva", "iva", "preço c/iva"],
    "marca": ["marca", "brand", "fornecedor", "fabricante", "supplier", "marca/fornecedor"],
    "categoria": ["subfamilia", "familia", "família", "categoria", "grupo", "category", "family", "tipo", "gama", "colecao", "coleção", "deporte"],
    "ean": ["ean 13", "ean13", "ean", "barcode", "codigo barras", "código barras", "cod barras", "gtin", "upc", "code"],
}

def normalize_col(col):
    c = str(col).strip().lower()
    c = re.sub(r'\s+', ' ', c)
    c = c.replace(".", "").replace("_", " ").strip()
    return c

def detect_columns(df_columns):
    mapping = {}
    normalized = {normalize_col(c): c for c in df_columns}
    used_cols = set()
    for field, aliases in COLUMN_ALIASES.items():
        found = None
        for alias in aliases:
            alias_norm = normalize_col(alias)
            # match exato
            if alias_norm in normalized:
                candidate = normalized[alias_norm]
                if candidate not in used_cols:
                    found = candidate
                    break
                # se já usado, tenta próximo alias
            # match contém (apenas se alias >3 para evitar falsos positivos)
            if not found:
                for norm, orig in normalized.items():
                    if orig in used_cols:
                        continue
                    if alias_norm == norm or alias_norm in norm or norm in alias_norm:
                        if len(alias_norm) <= 3 and alias_norm != norm:
                            continue
                        # evitar que 'codigo' mapeie para EAN se já foi usado para referencia
                        found = orig
                        break
            if found:
                break
        if found:
            mapping[field] = found
            used_cols.add(found)
    return mapping

def clean_stock(val):
    if pd.isna(val):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().lower()
    # tratar "sim"/"não", "x", "disponivel"
    if s in ["", "-", "n/a", "na", "null", "none"]:
        return 0
    if s in ["sim", "yes", "x", "disponível", "disponivel", "em stock"]:
        return 1
    if s in ["não", "nao", "no", "0", "indisponivel", "indisponível", "sem stock"]:
        return 0
    # remover unidades tipo "5 un"
    s = s.replace(",", ".")
    # extrair número
    m = re.search(r'-?\d+(\.\d+)?', s)
    if m:
        try:
            return int(float(m.group()))
        except:
            return 0
    return 0

def clean_preco(val):
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    s = s.replace("€", "").replace("eur", "").strip()
    # PT format 1.234,56 vs 1234.56
    # Se tem vírgula, assume PT
    if "," in s and "." in s:
        # 1.234,56 -> 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    m = re.search(r'-?\d+(\.\d+)?', s)
    if m:
        try:
            return float(m.group())
        except:
            return 0.0
    return 0.0

def parse_excel(file_path, sheet_name=0):
    """
    Lê Excel e retorna (rows, mapping, preview, warnings)
    rows: lista de dicts normalizados
    """
    # Tentar ler com openpyxl
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str, engine="openpyxl")
    except Exception as e:
        # tentar xlrd para .xls
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
        except Exception as e2:
            raise ValueError(f"Erro ao ler Excel: {e} / {e2}")

    # Limpeza inicial: remover linhas totalmente vazias
    df = df.dropna(how='all')
    # Remover colunas totalmente vazias
    df = df.dropna(axis=1, how='all')

    if df.empty:
        raise ValueError("Ficheiro vazio ou sem dados.")

    # Se header for na linha 1 mas parece ser dados? Detectar se primeira linha é header
    # Assumir que primeira linha já é header (pandas faz isso). Se colunas são Unnamed, tentar header na linha 0?
    # Simplificar: se colunas são 0,1,2... tentar primeira linha como header
    unnamed = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
    if unnamed > len(df.columns)/2:
        # Re-ler sem header
        try:
            df2 = pd.read_excel(file_path, sheet_name=sheet_name, header=None, dtype=str, engine="openpyxl")
            df2 = df2.dropna(how='all').dropna(axis=1, how='all')
            # primeira linha como header
            df2.columns = df2.iloc[0]
            df2 = df2[1:]
            df = df2
        except:
            pass

    # Limpar nomes de colunas duplicadas
    cols = []
    seen = {}
    for c in df.columns:
        c_str = str(c).strip()
        if c_str in seen:
            seen[c_str] += 1
            c_str = f"{c_str}_{seen[c_str]}"
        else:
            seen[c_str] = 0
        cols.append(c_str)
    df.columns = cols

    mapping = detect_columns(df.columns)

    warnings = []
    if "referencia" not in mapping:
        # Tenta usar primeira coluna como referencia
        warnings.append(f"Coluna 'referência' não detetada automaticamente. Usando primeira coluna '{df.columns[0]}' como referência.")
        mapping["referencia"] = df.columns[0]
    if "stock" not in mapping:
        warnings.append("Coluna 'stock' não detetada. Tentando adivinhar pela última coluna numérica...")
        # procurar coluna com mais valores numéricos
        best = None
        best_score = -1
        for col in df.columns:
            score = df[col].apply(lambda x: 1 if re.search(r'^\s*\d+', str(x)) else 0).sum()
            if score > best_score:
                best_score = score
                best = col
        if best:
            mapping["stock"] = best
        else:
            raise ValueError("Não foi possível detetar coluna de stock. Verifique mapeamento manual.")

    # Construir rows
    rows = []
    for idx, row in df.iterrows():
        ref = str(row.get(mapping["referencia"], "")).strip()
        if not ref or ref.lower() in ["nan", "none", "null", ""]:
            continue
        # Evitar linhas de total / header repetido
        if ref.lower() in ["referencia", "referência", "ref", "codigo"]:
            continue

        descricao = str(row.get(mapping.get("descricao", ""), "")).strip() if mapping.get("descricao") else ""
        if descricao.lower() in ["nan", "none"]:
            descricao = ""

        stock_raw = row.get(mapping["stock"], 0)
        stock = clean_stock(stock_raw)

        preco = 0.0
        if mapping.get("preco"):
            preco = clean_preco(row.get(mapping["preco"], 0))

        marca = str(row.get(mapping.get("marca", ""), "")).strip() if mapping.get("marca") else ""
        if marca.lower() in ["nan", "none"]:
            marca = ""
        categoria = str(row.get(mapping.get("categoria", ""), "")).strip() if mapping.get("categoria") else ""
        if categoria.lower() in ["nan", "none"]:
            categoria = ""
        ean = str(row.get(mapping.get("ean", ""), "")).strip() if mapping.get("ean") else ""
        if ean.lower() in ["nan", "none"]:
            ean = ""
        # extra: todas colunas não mapeadas
        extra = {}
        mapped_cols = set(mapping.values())
        for col in df.columns:
            if col not in mapped_cols:
                val = row.get(col, "")
                if pd.notna(val) and str(val).strip() not in ["", "nan"]:
                    extra[str(col)] = str(val).strip()

        rows.append({
            "referencia": ref,
            "descricao": descricao,
            "stock": stock,
            "preco": preco,
            "marca": marca,
            "categoria": categoria,
            "ean": ean,
            "extra": extra
        })

    if not rows:
        raise ValueError("Nenhum produto válido encontrado. Verifique se o ficheiro tem coluna de referência e stock.")

    # Preview (primeiras 5 linhas)
    preview = rows[:5]

    return rows, mapping, preview, warnings

def get_sheet_names(file_path):
    try:
        xl = pd.ExcelFile(file_path, engine="openpyxl")
        return xl.sheet_names
    except:
        try:
            xl = pd.ExcelFile(file_path)
            return xl.sheet_names
        except:
            return [0]
