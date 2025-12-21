import os,datetime
from factories.dataframe_ingestor import NC_DF_VOTER_CSV_Ingestor

# df1 = pd.read_csv(fn1, sep="\t", quotechar='"', engine="python", encoding="latin1",dtype=str)
# df2 = pd.read_csv(fn2, sep="\t", quotechar='"', engine="python", encoding="latin1",dtype=str)

# print(df1.columns)
# print(df2.columns)

# path = fn1
# created = ing.ingest_csv(path=path,chunksize=None, batch_size=100,standardize=True)
fn1 = os.path.expanduser("/home/Data/NC/ncvoter_Statewide_2017.txt.gz")
fn2 = os.path.expanduser("/home/Data/NC/ncvoter_Statewide_2025.v2.txt.gz")
# fn1 = os.path.expanduser("/home/Data/NC/old.csv")
# fn2 = os.path.expanduser("/home/Data/NC/new.csv")

ing = NC_DF_VOTER_CSV_Ingestor()

df_old = ing.load_and_standardize(fn1)
counts_old_df = (
    df_old.groupby(["race_code", "ethnic_code"])
      .size()
      .reset_index(name="count")
)
counts_old_df.to_csv('counts_old.csv')

df_new = ing.load_and_standardize(fn2)
counts_new_df = (
    df_new.groupby(["race_code", "ethnic_code"])
      .size()
      .reset_index(name="count")
)
counts_new_df.to_csv('counts_new.csv')

print(f"{datetime.datetime.now()} Combining dataframe" )

df_old_1 = df_old.drop_duplicates(subset=["name_hash", "address_hash"])
df_new_1 = df_new.drop_duplicates(subset=["name_hash", "address_hash"])

df_combined = df_old_1.merge(
    df_new_1,
    on=["name_hash", "address_hash"],
    how="inner",
    suffixes=("_old", "_new"),
)
# df_combined = df_old.merge(
#     df_new,
#     on=["name_hash", "address_hash"],
#     how="inner",
#     suffixes=("_old", "_new"),
# )
print(f"{datetime.datetime.now()} Calculating result" )


result_df = (
    df_combined.groupby(
        ["race_code_old", "ethnic_code_old",
         "race_code_new", "ethnic_code_new"]
    )
    .size()
    .reset_index(name="count")
)
print(f"{datetime.datetime.now()} Storing result" )

result_df.to_csv('result3.csv')
print(result_df)


print(f"{datetime.datetime.now()} Storing combined" )

df_combined.to_csv('out3.csv.gz',compression='infer',index=False)

result_df["old_key"] = result_df["race_code_old"] + "_" + result_df["ethnic_code_old"]
result_df["new_key"] = result_df["race_code_new"] + "_" + result_df["ethnic_code_new"]
matrix = result_df.pivot_table(index="old_key",columns="new_key",values="count",aggfunc="sum",fill_value=0)
matrix.to_csv('pivot_mtx3.csv')