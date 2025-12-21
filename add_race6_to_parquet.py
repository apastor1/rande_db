import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
from rande_geocoder.ruxton_geocode_lib import RXGeocoder,CensusHelper,GeoPy

# in_filepath = '/home/Data/GA/GA.CENSUS_VARS.csv.gz'
# out_filepath = '/home/Data/GA/GA.CENSUS_VARS.race6.csv.gz'
#in_filepath = '/home/Data/junk/GA.CENSUS_VARS.RACE.parquet'
#out_filepath = '/home/Data/junk/GA.CENSUS_VARS.RACE6.parquet'
#in_filepath = '/home/Data/junk/NC.CENSUS_VARS.parquet'
#out_filepath = '/home/Data/junk/NC.CENSUS_VARS.RACE6.parquet'
in_filepath = '/home/Data/junk/geolabel_income_votersGeo_expanded_new.parquet'
out_filepath = '/home/Data/junk/geolabel_income_votersGeo_expanded_new.RACE6.parquet'

# Read file metadata to get column names
pf = pq.ParquetFile(in_filepath)
cols = pf.schema.names

# Build a schema where all fields are string (utf8)
string_fields = [pa.field(c, pa.string()) for c in cols]
string_schema = pa.schema(string_fields)

# Read with forced schema
table = pq.read_table(in_filepath, schema=string_schema)
df = table.to_pandas()

#df = pd.read_parquet(in_filepath)
if 'census.state_id' in df.columns:
    df = CensusHelper.join_census_population_rates_race6(df=df,census_population_distribution_path='/home/Data/census/population_distribution_csv_race6/',
                                                    col_prefix=None,
                                                    state_id_col='census.state_id', county_id_col='census.county_id', tract_id_col='census.tract_id')
else:
    df = CensusHelper.join_census_population_rates_race6(df=df,census_population_distribution_path='/home/Data/census/population_distribution_csv_race6/',
                                                    col_prefix=None,
                                                    state_id_col='state_id', county_id_col='county_id', tract_id_col='tract_id')

#df.to_csv(out_filepath,compression='infer',index=False)
print(f'saving to {out_filepath}')
df.to_parquet(out_filepath, index=False)