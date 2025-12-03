import os
from factories.dataframe_ingestor import NC_DF_VOTER_CSV_Ingestor
fn1 = os.path.expanduser("~/Downloads/junk2017.txt.gz")
fn2 = os.path.expanduser("~/Downloads/junk2025.txt.gz")

# df1 = pd.read_csv(fn1, sep="\t", quotechar='"', engine="python", encoding="latin1",dtype=str)
# df2 = pd.read_csv(fn2, sep="\t", quotechar='"', engine="python", encoding="latin1",dtype=str)

# print(df1.columns)
# print(df2.columns)

# path = fn1
# created = ing.ingest_csv(path=path,chunksize=None, batch_size=100,standardize=True)

ing = NC_DF_VOTER_CSV_Ingestor()

df_old = ing.load_and_standardize(fn1)
df_new = ing.load_and_standardize(fn2)
df_combined = df_old.merge(
    df_new,
    on=["name_hash", "address_hash"],
    how="inner",
    suffixes=("_old", "_new"),
)
df_combined.to_csv('out.csv.gz',compression='infer',index=False)
