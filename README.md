# ERP Stock — TeamBike / SportMed

ERP simples para monitorizar stock a partir de ficheiros Excel do fornecedor.

## Como usar

### 1. Instalar
```bash
pip install -r requirements.txt
# ou
pip install --break-system-packages -r requirements.txt  # macOS Homebrew
```

### 2. Correr
```bash
python app.py
# ou
./run.sh
```
Abre em **http://localhost:5000**

## Funcionalidades

| Aba | Descrição |
|-----|-----------|
| **Stock — TeamBike** | Todos os produtos do último import. Pesquisa, filtros por stock/marca, botão Monitorizar. |
| **Verificação** | Compara últimas 2 importações: Reposições (0→>0), Ruturas (>0→0), Novos, Abaixo do Limite (monitorizados), Desaparecidos. |
| **Monitorizados** | Lista de produtos acompanhados. Editar limite individual, remover. |
| **Importar** | Arrastar Excel (.xlsx/.xls/.csv). Guarda histórico e atualiza Verificação automaticamente. |
| **Definições** | Limite padrão, limpar dados, estatísticas. |
| **Ajuda** | Guia de uso. |

## Formato do Excel

O parser deteta automaticamente colunas com nomes flexíveis (PT/EN):

- **Referência**: `Referência`, `Ref`, `Código`, `SKU`, `Artigo`
- **Descrição**: `Descrição`, `Designação`, `Nome`, `Produto`
- **Stock**: `Stock`, `Qtd`, `Quantidade`, `Disponível`
- **Preço**: `Preço`, `P.V.P.`, `Preço Venda`, `Valor`
- **Marca**: `Marca`, `Fornecedor`, `Brand`
- **Categoria**: `Categoria`, `Família`, `Grupo`
- **EAN**: `EAN`, `Barcode`, `Código Barras`

Colunas extra são guardadas e mostradas.

> **Importante**: O ficheiro importado deve conter a lista **completa** do fornecedor. Produtos que não apareçam são marcados como "Desaparecidos" e removidos do Stock atual (mantidos no histórico).

## Estrutura
```
Pedro2/
├── app.py              # Flask app
├── database.py         # SQLite (data/erp.db)
├── excel_parser.py     # Detecção inteligente de colunas
├── templates/index.html
├── data/erp.db
├── TeamBike/           # Exemplos
├── SportMed/
└── requirements.txt
```

## Dados de exemplo

Já incluídos 3 ficheiros de demo:
- `TeamBike/Stock_TeamBike_2024-01-15.xlsx` (30 produtos)
- `TeamBike/Stock_TeamBike_2024-02-01.xlsx` (32 produtos — com reposições/ruturas/novos para demo)
- `SportMed/Stock_SportMed_2024-01-15.xlsx` (4 produtos, formato diferente)

A BD já vem com 2 importações e 3 produtos monitorizados para ver a Verificação a funcionar.

## Base de dados

SQLite com tabelas: `products`, `imports`, `import_products` (snapshot por import), `monitored`, `settings`.

Limite padrão: 0 (alerta se ficar sem stock). Alterável por produto nos Monitorizados ou nas Definições.
# erp-stock
