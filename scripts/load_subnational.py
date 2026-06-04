"""One-off fast bulk loader for the 3 subnational fact tables into an existing
forest.db (schema already created by the main pipeline). Usage:
    python scripts/load_subnational.py tree|primary|carbon|all
"""
import sys, time, sqlite3
sys.path.insert(0, "src")
import polars as pl
from nexus.config.settings import settings
from nexus.data.pipeline.loaders import ExcelLoader
from nexus.data.pipeline.cleaners import DataCleaner
from nexus.data.pipeline.transformers import (
    SubnationalTreeCoverTransformer,
    SubnationalPrimaryForestTransformer,
    SubnationalCarbonTransformer,
)

def bulk_insert(con, table, df):
    cols = df.columns
    qcols = ",".join(f'"{c}"' for c in cols)
    ph = ",".join("?" for _ in cols)
    cur = con.cursor()
    cur.execute(f"DELETE FROM {table}")
    cur.executemany(f"INSERT INTO {table} ({qcols}) VALUES ({ph})", df.iter_rows())
    con.commit()
    n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table}: {n} rows")

def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    loader = ExcelLoader()
    cleaner = DataCleaner()
    con = sqlite3.connect(settings.sqlite_db_path)
    # speed pragmas for bulk load
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA journal_mode=MEMORY")
    con.execute("PRAGMA temp_store=MEMORY")

    if which in ("tree", "all"):
        t = time.time()
        df = cleaner.clean_country_names(loader.load_subnational_tree_cover_loss())
        fact = SubnationalTreeCoverTransformer().transform(df)
        bulk_insert(con, "fact_subnational_tree_cover_loss", fact)
        print(f"  tree done in {time.time()-t:.1f}s")
    if which in ("primary", "all"):
        t = time.time()
        df = cleaner.clean_country_names(loader.load_subnational_primary_forest())
        fact = SubnationalPrimaryForestTransformer().transform(df)
        bulk_insert(con, "fact_subnational_primary_forest", fact)
        print(f"  primary done in {time.time()-t:.1f}s")
    if which in ("carbon", "all"):
        t = time.time()
        df = cleaner.clean_country_names(loader.load_subnational_carbon_data())
        fact = SubnationalCarbonTransformer().transform(df)
        bulk_insert(con, "fact_subnational_carbon", fact)
        print(f"  carbon done in {time.time()-t:.1f}s")
    con.close()

if __name__ == "__main__":
    main()
