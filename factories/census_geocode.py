# geocoding_run.py
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # your preference: always load .env
from tqdm import tqdm
import os, glob, csv, tempfile
import time
import hashlib
import subprocess
from typing import Iterable, List, Optional, Tuple, Dict, Any
from rande_geocoder.ruxton_geocode_lib import CensusHelper, CensusGeocode

import numpy as np
import pandas as pd

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sql_orm import new_uuid_str, CensusGeocode as CensusGeocodeTable  # from your models

class DEFUNCTCensusHelper(object):
    @staticmethod
    def get_tab_block_shapefiles(dest_path:str=None):
        """
        Gets block shapefiles from census
        """
        # Base FTP URL for 2020 tabulation block shapefiles
        base_url = "ftp://ftp2.census.gov/geo/tiger/TIGER2020/TABBLOCK20"
        # Container for each state’s tract-level summary
        tract_summaries = []
        for state in tqdm(range(2, 79), desc="States processed"):
            fips = f"{state:02d}"
            zip_name = f"tl_2020_{fips}_tabblock20.zip"
            url = f"{base_url}/{zip_name}"
            if dest_path:
                result = subprocess.run(["wget", "-q", url,"-P",dest_path], capture_output=True)
            else:
                result = subprocess.run(["wget", "-q", url], capture_output=True)
            if result.returncode != 0:
                print(f"Warning: Failed to download {zip_name}, skipping.")
                continue
        return

    @staticmethod
    def convert_tab_block_file_to_tract(census_path:str=None,file_mask="tl_2020_*_tabblock20.zip"):
        """
        Aggregates block level data into tract and county level. population, and U for urban or R for rural
        """
        if type(census_path)!=str:
            census_path = ''
        tract_df = pd.DataFrame()
        county_df = pd.DataFrame()
        zip_files = glob.glob(os.path.join(census_path,file_mask))
        for zip_name in sorted(zip_files):
            try:
                print(f"Reading {zip_name}")
                gdf = gpd.read_file(f"zip://{zip_name}")
            except Exception as e:
                print(f"Error reading {zip_name}: {e}")
                #os.remove(zip_name)
                continue
            # Select necessary fields and derive tract or county ID
            df = gdf[["GEOID20", "UR20", "POP20"]].copy()
            for is_county in [False,True]:
                if not is_county:
                    df["GEOID"] = df["GEOID20"].str[:11] # 2 state, 3 county, 6 tract
                else:
                    df["GEOID"] = df["GEOID20"].str[:5] # 2 state, 3 county
                # Aggregate—selecting only UR & POP20 so TRACT20 never enters apply()
                agg = (
                    df
                    .groupby("GEOID")[["UR20","POP20"]]
                    .apply(lambda blocks: pd.Series({
                        "POP": blocks["POP20"].sum(),
                        "UR": "U" if blocks.loc[blocks["UR20"]=="U","POP20"].sum() >= 0.5 * blocks["POP20"].sum() else "R"
                    }))
                    .reset_index()
                )
                if not is_county:
                    tract_df = pd.concat([tract_df, agg], ignore_index=True)
                else:
                    county_df = pd.concat([county_df, agg], ignore_index=True)
        tract_df.to_csv("tract_urban_rural_2020.csv.zip", compression='infer', index=False)
        county_df.to_csv("county_urban_rural_2020.csv.zip", compression='infer', index=False)
        return

    @staticmethod
    def get_unique_geo_df(df, state_id_col='census.state_id', county_id_col='census.county_id', tract_id_col='census.tract_id'):
        """
        Gets unique census geographies represented in dataframe
        """
        if df is None or len(df)<1:
            return None

        aggregating_columns = [state_id_col,county_id_col,tract_id_col]
        df_cols = df.columns
        for c in aggregating_columns:
            if c not in df_cols:
                raise Exception('get_unique_geo_df did not find column %s in input dataframe. need following all columns %r' % (c,aggregating_columns))
        unique_geo_df = df[aggregating_columns].value_counts().reset_index(name='count')
        if unique_geo_df is not None:
            unique_geo_df.sort_values(by=[state_id_col], inplace=True)
        return unique_geo_df

    @staticmethod
    def get_state_code_dict(filename='/home/Data/census/state_codes.csv'):
        """
        Creates dictionary mapping state two letter code, like FL to integer census code int, like 12
        """
        df = pd.read_csv(filename,sep=",",compression='infer',low_memory=False)
        state_code_dict = dict()
        numcode_idx =  df.columns.get_loc('NumericCode')
        let_code_idx = df.columns.get_loc('LetterCode')
        for i in range(len(df)):
            state_code_dict[df.iloc[i,numcode_idx]] = df.iloc[i, let_code_idx]
        return state_code_dict

    @staticmethod
    def get_one_census_population_rates(state_code:str,county_id:int,tract_id:int,census_population_distribution_path:str='/home/Data/census/population_distribution_csv'):
        """
        Gets census population numbers and rates for a given state code (FL), county_id and tract_id (integers)
        """
        census_filename = os.path.join(census_population_distribution_path,'census-2020-tract-%s.csv.gz' % state_code)
        try:
            census_geo_df = pd.read_csv(census_filename,sep=",",compression='infer',low_memory=False)
        except:
            print('WARNING: unable to load census population rates for state %s. File %s' % (state_code,census_filename))
            census_geo_df = None
    
        if census_geo_df is None:
            print('Could not find file %s' % census_filename)

        res_census_geo_df = census_geo_df.loc[(census_geo_df['state'] == state_code) & (census_geo_df['county'] == county_id) & (census_geo_df['tract'] == tract_id)]
        return res_census_geo_df

    @staticmethod
    def create_hashtable(df,state_id_col='census.state_id', county_id_col='census.county_id', tract_id_col='census.tract_id'):
        hash_list = list()
        hash_col_name = 'hash'
        N = len(df)
        ruxton_lib.print_time(msg='Preparing hashtable(geoids) for %d unique records' % N)
        start_t = time.time()
        n_step = 1000000
        for i,row in df.iterrows():
            if i % n_step ==0:
                elapsed_t = time.time()-start_t
                eta = elapsed_t/i*(N-i)/3600 if i else np.nan
                ruxton_lib.print_time(msg="Preparing hashtable: On index %d of %d. ETA %.2f hours" % (i,N,eta))
            try:
                hash = "%02d%03d%06d" % (int(round(float(row[state_id_col]))),int(round(float(row[county_id_col]))),int(round(float(row[tract_id_col]))))
            except:
                hash = ''
            hash_list.append(hash)
        hash_df = pd.DataFrame({hash_col_name:hash_list})

        hash_table = dict()
        print("%s: Generating master hashtable" % (datetime.now()),flush=True)
        for i in tqdm(range(N)):
            hash = hash_df.loc[i,hash_col_name]
            hash_table.setdefault(hash, []).append(i)
        return hash_table

    @staticmethod
    def join_census_population_rates(df,census_population_distribution_path='/home/Data/census/population_distribution_csv', col_prefix:str='', state_id_col='census.state_id', county_id_col='census.county_id', tract_id_col='census.tract_id'):
        """
        Augments dataframe by joining population rates based on census state,county,tract
        """

        state_code_dict = CensusHelper.get_state_code_dict()
        unique_geo_df = CensusHelper.get_unique_geo_df(df,state_id_col=state_id_col, county_id_col=county_id_col, tract_id_col=tract_id_col)
        ruxton_lib.print_time(msg='Starting analysis for %d unique tracts' % len(unique_geo_df))
        hash_table = CensusHelper.create_hashtable(df=df,state_id_col=state_id_col, county_id_col=county_id_col, tract_id_col=tract_id_col)
        predict_race_list = sorted(ruxton_lib.map_english_to_race5.keys())
        pop_var_name = ('%s_pop_rate' % col_prefix) if (col_prefix is not None or (type(col_prefix)==str and col_prefix!='')) else 'pop_rate'
        for race in predict_race_list:
            df['%s_%s' % (pop_var_name,race)] = np.nan
        cur_state_code = None
        N = len(unique_geo_df)
        start_t = time.time()
        n_step = 500
        for i in range(N):
            if i % n_step ==0:
                elapsed_t = time.time()-start_t
                eta = elapsed_t/i*(N-i)/3600 if i else np.nan
                print("%s: Population rates: On index %d of %d. ETA %.2f hours" % (datetime.now(),i,N,eta),flush=True)
                #ruxton_lib.print_time(msg='Population rates: On unique tract %d of %d' % (i,len(unique_geo_df)))
            # find the information for census geo (probabilities)
            state_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(state_id_col)]
            county_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(county_id_col)]
            tract_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(tract_id_col)]
            try:
                hash = "%02d%03d%06d" % (int(round(float(state_id))),int(round(float(county_id))),int(round(float(tract_id))))
            except:
                hash = ''

            try:
                state_code = state_code_dict[int(state_id)]
            except:
                print('WARNING: unknown state_code "%r"' % state_id,flush=True)
                continue

            # only load new state file, when required
            if state_code != cur_state_code:
                cur_state_code = state_code
                census_filename = os.path.join(census_population_distribution_path,'census-2020-tract-%s.csv.gz' % cur_state_code)
                try:
                    print('Working on state %s' % state_code)
                    census_geo_df = pd.read_csv(census_filename,sep=",",compression='infer',low_memory=False,dtype=str)
                except:
                    print('WARNING: unable to load census population rates for state %s. File %s' % (cur_state_code,census_filename))
                    census_geo_df = None
            
            if census_geo_df is None:
                continue

            # print("GRRR",state_code)
            # print(type(state_code),state_code)
            county_id_str = "%03d" % int(round(float(county_id)))
            tract_id_str = "%06d" % int(round(float(tract_id)))
            res_census_geo_df = census_geo_df.loc[(census_geo_df['state'] == state_code) & (census_geo_df['county'] == county_id_str) & (census_geo_df['tract'] == tract_id_str)]
            # print(len(res_census_geo_df))
            # print(census_geo_df.columns)
            # print('%s/%s/%s :: %s/%s' % (state_id,county_id,tract_id,county_id_str,tract_id_str))
            # mask = census_geo_df['state'] == state_code; print(np.count_nonzero(mask))
            # mask = census_geo_df['county'] == county_id_str; print(np.count_nonzero(mask))
            # mask = census_geo_df['tract'] == tract_id_str; print(np.count_nonzero(mask))
            # input("GRRR2")            

            
            #match_mask = (df[state_id_col] == state_id) & (df[county_id_col] == county_id) & (df[tract_id_col] == tract_id)
            for race in predict_race_list:
                pop_rate_name = '%s_%s' % (pop_var_name,race)
                census_pop_rate_name = 'pop_rate_%s' % race
                # print(census_pop_rate_name)
                # print(res_census_geo_df.iloc[0])
                # input('kuku')
                try:
                    census_pop_rate = float(res_census_geo_df.iloc[0,res_census_geo_df.columns.get_loc(census_pop_rate_name)])
                    #df.loc[match_mask, pop_rate_name] = census_pop_rate
                    df.loc[hash_table[hash], pop_rate_name] = census_pop_rate
                except Exception as ex:
                    print('WARNING: did not find value for %s given state/county/tract ids of %s/%s/%s\n%s' % (pop_rate_name,state_id,county_id,tract_id,ex))

        return df

    @staticmethod
    def join_census_variables(df,dest_col_name,census_var_name, hash_table=None,main_dir='/home/Data/census',dataset='acs5',year=2020,state_id_col='census.state_id', county_id_col='census.county_id', tract_id_col='census.tract_id'):
        """
        Augments dataframe with new variable based on variable stored in file_prefix+'.'+state_code+'.'+file_suffix
        df - input dataframe, which will be augmented
        dest_col_name - columne taht will be overwritted or added, if missing
        census_var_name - census variable like 'B19013_001E',
        file_prefix - eg '/home/Data/census/income/B19013_001E/census_median_income_2020'
        file_suffix - eg 'csv.gz'
        """
        census_api_key = os.environ.get('CENSUS_API_KEY',None)
        unique_geo_df = CensusHelper.get_unique_geo_df(df,state_id_col=state_id_col, county_id_col=county_id_col, tract_id_col=tract_id_col)
        state_code_dict = CensusHelper.get_state_code_dict()
        ruxton_lib.print_time(msg='Starting analysis for %d unique tracts' % len(unique_geo_df))
        predict_race_list = sorted(ruxton_lib.map_english_to_race5.keys())
        if hash_table is None:
            hash_table = CensusHelper.create_hashtable(df=df,state_id_col=state_id_col, county_id_col=county_id_col, tract_id_col=tract_id_col)
        df[dest_col_name] = np.nan
        cur_state_code = None
        N = len(unique_geo_df)
        start_t = time.time()
        n_step=500
        
        table_name = census_var_name.split('_')[0]
        census_filename = os.path.join(main_dir,dataset,"%s" % year,table_name,"%s.csv.gz" % "ALL")
        #census_filename = join_fmt % (file_prefix,'ALL',file_suffix)
        #print(census_filename)
        if not os.path.exists(census_filename):
            dextractor = DataExtractor(census_api_key=census_api_key,main_dir=main_dir)
            dextractor.get_dataframe_pull_if_missing(year=year,dataset=dataset,var_name=census_var_name,state_abbr=None)

        for i in range(N):
            if i % n_step ==0:
                elapsed_t = time.time()-start_t
                eta = elapsed_t/i*(N-i)/3600 if i else np.nan
                print("%s: Census variable %r on index %d of %d. ETA %.2f hours" % (datetime.now(),census_var_name,i,N,eta),flush=True)
                #ruxton_lib.print_time(msg='Census variable %r : On unique tract %d of %d' % (census_var_name,i,len(unique_geo_df)))

            # find the information for census geo (probabilities)
            state_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(state_id_col)]
            county_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(county_id_col)]
            tract_id = unique_geo_df.iloc[i, unique_geo_df.columns.get_loc(tract_id_col)]
            try:
                state_code = state_code_dict[int(state_id)]
            except:
                print('WARNING: unknown state_code "%r"' % state_id,flush=True)
                continue

            try:
                hash = "%02d%03d%06d" % (int(round(float(state_id))),int(round(float(county_id))),int(round(float(tract_id))))
            except:
                hash = ''
 
            # only load new state file, when required
            if state_code != cur_state_code:
                cur_state_code = state_code

                #census_filename = '%s.%s.%s' % (file_prefix,cur_state_code,file_suffix)
                #census_filename = join_fmt % (file_prefix,cur_state_code,file_suffix)
                census_filename = os.path.join(main_dir,dataset,"%s" % year,table_name,"%s.csv.gz" % state_code)
                try:
                    census_df = pd.read_csv(census_filename,sep=",",compression='infer',low_memory=False,dtype=str)
                except:
                    print('WARNING: unable to load census variables rates for state %s. File %s' % (cur_state_code,census_filename))
                    census_df = None

            
            if census_df is None or len(census_df)<1 or 'county' not in census_df.columns:
                print('State %r had no census data' % state_code)
                continue

            res_census_df = census_df.loc[(census_df['state_code'] == state_code) & (census_df['county'] == county_id) & (census_df['tract'] == tract_id)]
            if len(res_census_df)==0:
                res_census_df = census_df.loc[(census_df['state_code'] == state_code) & (census_df['county'].astype(int) == int(county_id)) & (census_df['tract'].astype(int) == int(tract_id))]
            
            #match_mask = (df[state_id_col] == state_id) & (df[county_id_col] == county_id) & (df[tract_id_col] == tract_id)
            try:
                census_value = float(res_census_df.iloc[0,res_census_df.columns.get_loc(census_var_name)])
                #df.loc[match_mask, dest_col_name] = census_value
                df.loc[hash_table[hash], dest_col_name] = census_value
            except Exception as ex:
                print('WARNING: did not find value for %s given state/county/tract ids of %s/%s/%s\n%s' % (census_var_name,state_id,county_id,tract_id,ex))

        return df

    @staticmethod
    def format_address(df_row,zip_col='zip',street_col='street',city_col='city',state_col='sources'):
        """
        """
        zipcode = df_row[zip_col]
        try:
            zipcode = int(zipcode)
        except:
            pass
        if type(zipcode)==int and zipcode>100000:
            zipcode = int(str(zipcode)[0:5])
        street = df_row[street_col]
        try:
            street = street.strip()
            street = ''.join(i for i in street if ord(i)<128 and ord(i)>31)
        except:
            return None
        city = df_row[city_col]
        try:
            city = city.strip()
            city = ''.join(i for i in city if ord(i)<128 and ord(i)>31)

        except:
            return None
        state = df_row[state_col]
        try:
            state = state.strip()
            state = ''.join(i for i in state if ord(i)<128 and ord(i)>31)
        except:
            return None
        
        if street=='' or city=='' or state=='':
            return None
        
        address = '%s, %s, %s, %s' % (street,city,state,zipcode)
        address = address.replace("  "," ")
        address = address.replace("  "," ")
        return address

    @staticmethod
    def get_zipcode(df_row,cfg):
        col_list = cfg.get('zipcode',[])
        zipcode = ''.join(str(x) for x in list(df_row[col_list].values.flatten()))
        try:
            zipcode = int(zipcode)
        except:
            pass
        if type(zipcode)==int and zipcode>100000:
            zipcode = int(str(zipcode)[0:5])
        else:
            zipcode = str(zipcode).strip()

        return zipcode

    @staticmethod
    def get_street(df_row,cfg):
        col_list = cfg.get('street',[])
        street = ' '.join(str(x) if type(x)==str else '' for x in list(df_row[col_list].values.flatten()))
        street = street.strip()
        return street

    @staticmethod
    def get_city(df_row,cfg):
        col_list = cfg.get('city',[])
        city = ' '.join(str(x) for x in list(df_row[col_list].values.flatten()))
        city = city.strip()
        return city

    @staticmethod
    def get_state(df_row,cfg):
        col_list = cfg.get('state',[])
        state = ' '.join(str(x) for x in list(df_row[col_list].values.flatten()))
        state = state.strip()
        return state

    @staticmethod
    def format_address_general(df_row,cfg):
        address = None
        zipcode = CensusHelper.get_zipcode(df_row,cfg)
        street = CensusHelper.get_street(df_row,cfg)
        city = CensusHelper.get_city(df_row,cfg)
        state = CensusHelper.get_state(df_row,cfg)
        force_dict = cfg.get('force',None)
        if force_dict:
            state_force = force_dict.get('state',None)
            if state!=state_force:
                zipcode = '' # zipcode in wrong state, skip
            if state_force:
                state = state_force
            city_force = force_dict.get('city',None)
            if city_force:
                city = city_force
        if street=='' or city=='' or state=='':
            return None

        address = '%s, %s, %s, %s' % (street,city,state,zipcode)
        address = address.replace(" ,",",")
        tmp = ''
        while tmp!=address:
            tmp = address
            address = tmp.replace("  "," ")

        # get rid of extra characters
        address = ''.join(i for i in address if ord(i)<128 and ord(i)>31)
        return address

    @staticmethod
    def format_address_fields(df_row,address_fields,address_fmt):
        field_values = list()
        for field in address_fields:
            v = str(df_row[field])
            v = v.strip()
            v = ''.join(c for c in v if ord(c)<128 and ord(c)>31) # get rid of non-english characters
            v = v.replace(',',' ')
            v = v.replace('  ',' ')
            field_values.append(v)
        
        address = address_fmt % tuple(field_values)
        return address

    @staticmethod
    def DEFUNCT_create_census_file(df,block_idx_ar,chunk_file,cfg):
        # create chunk file
        t = time.time()
        request_count = 0
        f = open(chunk_file,'w')
        for i in range(len(block_idx_ar)):
            #address = format_address(df_row=df.iloc[block_idx_ar[i]],zip_col=zip_col,street_col=street_col,city_col=city_col,state_col=state_col)
            address = CensusHelper.format_address_general(df_row=df.iloc[block_idx_ar[i]],cfg=cfg)
            if address:
                f.write('%d, %s\n' % (i,address))
                request_count += 1
        f.close()
        return request_count

    @staticmethod
    def create_census_file(df,block_idx_ar,chunk_file,address_fields,address_fmt):
        # create chunk file
        t = time.time()
        request_count = 0
        f = open(chunk_file,'w')
        for i in range(len(block_idx_ar)):
            #address = format_address(df_row=df.iloc[block_idx_ar[i]],zip_col=zip_col,street_col=street_col,city_col=city_col,state_col=state_col)
            address = CensusHelper.format_address_fields(df_row=df.iloc[block_idx_ar[i]],address_fields=address_fields,address_fmt=address_fmt)
            if address:
                f.write('%d, %s\n' % (i,address))
                request_count += 1
        f.close()
        return request_count


    @staticmethod
    def block_to_census(cg,chunk_file,n_max_tries,timeout):
        print("Will attempt to pull data for %s from CENSUS; %d tries with %dsec timeout." % (chunk_file,n_max_tries,timeout))

        t = time.time()
        n_try = 0
        result = None
        while (result is None and n_try<=n_max_tries-1):
            result = None
            try:
                # this is where we send an address batch request
                result = cg.addressbatch(chunk_file, returntype="geographies",timeout=timeout)
            except:
                n_try += 1
                print("Census timed out %d sec for %s" % (timeout,chunk_file),flush=True)
                #time.sleep(sleep_t)
        dt = time.time()-t;t = time.time()
        print("Census request took %.1f sec for %s" % (dt,chunk_file),flush=True)
        return result

    @staticmethod
    def block_to_census_one_address(cg,chunk_file,n_max_tries,timeout):
        result = None
        with open(chunk_file) as f:
            lines = [line.strip() for line in f]
        K = len(lines)
        block_t = time.time()
        for i,line in enumerate(lines):
            print("PULLING",i,line)
            #if True:
            try:
                (idx,street,city,state,zipcode) = line.split(",")
                n_try = 0
                rr = None
                while (result is None and n_try<=n_max_tries-1):
                    rr = None
                    if True:
                    #try:
                        #print('will request: ',street,' ',city,' ',state,' ',zipcode,' ',n_try,' ',n_max_tries)
                        # this is where we send an address batch request
                        rr = cg.address(street,city,state,zip=zipcode,timeout=timeout)
                        #print(rr)
                        #cg.addressbatch(chunk_file, returntype="geographies",timeout=timeout)
                        n_try += 1
                        print('requesting: ',street,city,state,zipcode,n_try,n_max_tries,rr)
                    else:
                    #except:
                        n_try += 1
                        print("Census timed out %d sec for %s" % (timeout,chunk_file))
                        #time.sleep(sleep_t)
                if rr is None:
                    time.sleep(0.5)
                    print(".",end="")
                #dt = time.time()-t;t = time.time()
                #print("Census request took %.1f sec for %s" % (dt,chunk_file))

                #rr  = cg.address(street,city,state,zip=zipcode)
                r = dict()
                #print(line,rr)
                #input("Next?")
                if rr and len(rr)>0:
                    z = rr[0] # may have more tahn one entry. not sure what to do with that
                    r = dict()
                    r["id"]          = idx
                    r["match"]       = True
                    r["matchtype"]   = "one_address"
                    r["parsed"]      = z['matchedAddress']
                    r["tigerlineid"] = z['tigerLine']['tigerLineId']    
                    r["statefp"]     = z['geographies']['Census Blocks'][0]['STATE']
                    r["countyfp"]    = z['geographies']['Census Blocks'][0]['COUNTY']
                    r["tract"]       = z['geographies']['Census Blocks'][0]['TRACT']
                    r["block"]       = z['geographies']['Census Blocks'][0]['BLOCK']
                    r["lat"]         = z['geographies']['Census Blocks'][0]['CENTLAT']
                    r["lon"]         = z['geographies']['Census Blocks'][0]['CENTLON']

                    if result:
                        result.append(r)
                    else:
                        result = [r]
                    print('length or result',len(result))
                    #input("Next?")
                if i % 100==0:
                    elapsed_t = time.time()-block_t
                    line_t = 1.*elapsed_t/(i+1)
                    eta = line_t*(K-(i+1))
                    print("Workign on line %d of block of %d. ETA %.2fsec" % (i+1,K,eta))
            #else:
            except:
                pass
        return result

    @staticmethod
    def update_block(df,block_idx_ar,result):
        # get column name dictionary
        col_idx_dict = dict()
        for c in df.columns:
            col_idx_dict[c]= df.columns.get_loc(c)

        if result is None:
            print("Empty result, nothing to do")
            return
        
        # update array
        fail_count = 0
        df.loc[block_idx_ar, 'census.attempted'] = True
        for r in result:
            try:
                block_idx = block_idx_ar[int(r["id"])]
                df.iat[block_idx,col_idx_dict["census.match"]] = r["match"]    
                df.iat[block_idx,col_idx_dict["census.matchtype"]] = r["matchtype"]    
                df.iat[block_idx,col_idx_dict["census.parsed"]] = r["parsed"]    
                df.iat[block_idx,col_idx_dict["census.tigerlineid"]] = r["tigerlineid"]    
                df.iat[block_idx,col_idx_dict["census.state_id"]] = r["statefp"]    
                df.iat[block_idx,col_idx_dict["census.county_id"]] = r["countyfp"]    
                df.iat[block_idx,col_idx_dict["census.tract_id"]] = r["tract"]    
                df.iat[block_idx,col_idx_dict["census.block_id"]] = r["block"]    
                df.iat[block_idx,col_idx_dict["census.lat"]] = r["lat"]    
                df.iat[block_idx,col_idx_dict["census.lon"]] = r["lon"]
                if r["block"] is None:
                    fail_count += 1
            except:
                print("Error with row %r" % r)
                fail_count += 1
        print("Failure rate %.2f%%" % (100.*fail_count/len(block_idx_ar)))
        return

# ---------- Census client (pluggable) ----------

class CensusClient:
    """
    Abstract client. Implement `send_batch` for the real Census API.
    Must return a list of dicts: {"address_hash", "geoid", "status", "result"}.
    - address_hash: string (matches input)
    - geoid: string or None
    - status: e.g., "matched" | "no_match" | "ambiguous"
    - result: arbitrary JSON payload from provider
    """
    def send_batch(
        self,
        rows: List[Tuple[str, str]],
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError


class MockCensusClient(CensusClient):
    """
    Mock implementation for local testing. Pretends to "geocode" addresses.
    - If canonical contains "po box" => no_match
    - Else returns a fake GEOID derived from md5(address_canonical).
    """
    def send_batch(
        self,
        rows: List[Tuple[str, str]],
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for address_hash, canonical in rows:
            canon_lc = (canonical or "").lower()
            if "po box" in canon_lc:
                out.append({
                    "address_hash": address_hash,
                    "geoid": None,
                    "status": "no_match",
                    "result": {"reason": "po_box_filtered", "benchmark": benchmark, "vintage": vintage},
                })
            else:
                # Derive a deterministic fake GEOID for demo purposes
                geoid = hashlib.md5(canon_lc.encode("utf-8")).hexdigest()[:12]
                out.append({
                    "address_hash": address_hash,
                    "geoid": geoid,
                    "status": "matched",
                    "result": {"mock": True, "benchmark": benchmark, "vintage": vintage},
                })
        # Simulate network latency
        time.sleep(0.2)
        return out


class BatchCensusClient(CensusClient):
    """
    Real Census client using our internal CensusGeocode + CensusHelper.block_to_census.

    - Input rows: (address_hash, address_canonical)
      * address_canonical is expected to be "street, city, state, zip"
        (same thing RXGeocoder.format_address_fields generates).
    - Uses the Census batch API (addressbatch) via CensusGeocode.
    - Returns records shaped like MockCensusClient:
        {"address_hash", "geoid", "status", "result"}.
    """

    def __init__(self, *, n_max_tries: int = 3, timeout: int = 450) -> None:
        self.n_max_tries = n_max_tries
        self.timeout = timeout

    # ---------- helpers ----------

    def _write_chunk_file(
        self,
        rows: List[Tuple[str, str]],
    ) -> str:
        """
        Create a temp CSV file in the format the Census batch API expects:

            id,street,city,state,zip

        We assume address_canonical is already: "street, city, state, zip".
        If it's not, you can adjust the parsing below.
        """
        fd, path = tempfile.mkstemp(prefix="census_batch_", suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # No header: Census endpoint expects raw rows
            for idx, (_addr_hash, canonical) in enumerate(rows):
                canonical = canonical or ""
                # Try to split into 4 parts: street, city, state, zip
                parts = [p.strip() for p in canonical.split(",")]

                if len(parts) >= 4:
                    street, city, state, zipcode = parts[0], parts[1], parts[2], parts[3]
                else:
                    # Fallback: shove everything into street and leave city/state/zip blank
                    street, city, state, zipcode = canonical.strip(), "", "", ""

                writer.writerow([idx, street, city, state, zipcode])

        return path

    def _map_census_result(
        self,
        rows: List[Tuple[str, str]],
        raw: List[Dict[str, Any]] | None,
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        """
        Convert the list of dicts from CensusHelper.block_to_census(...) into
        the structure expected by upsert_census_results.
        """
        out: List[Dict[str, Any]] = []

        # index -> address_hash
        idx_to_hash = [address_hash for address_hash, _canon in rows]
        seen_hashes: set[str] = set()

        if not raw:
            # No response at all (timeout / error): everything is no_match
            for address_hash, _canon in rows:
                out.append({
                    "address_hash": address_hash,
                    "geoid": None,
                    "status": "no_match",
                    "result": {
                        "reason": "no_response_from_census",
                        "benchmark": benchmark,
                        "vintage": vintage,
                    },
                })
            return out

        for r in raw:
            # r is produced by CensusGeocode._parse_batch_result via block_to_census:
            # keys include: id, match, matchtype, parsed, tigerlineid,
            #               statefp, countyfp, tract, block, lat, lon, ...
            try:
                idx = int(r["id"])
            except Exception:
                # If `id` is missing or malformed, skip it
                continue

            if idx < 0 or idx >= len(idx_to_hash):
                # Out-of-range id
                continue

            address_hash = idx_to_hash[idx]
            seen_hashes.add(address_hash)

            match = bool(r.get("match"))
            matchtype = (r.get("matchtype") or "").lower()

            statefp = r.get("statefp")
            countyfp = r.get("countyfp")
            tract = r.get("tract")
            block = r.get("block")

            # Default
            geoid = None
            status = "no_match"

            if match and statefp and countyfp and tract and block:
                geoid = f"{statefp}{countyfp}{tract}{block}"

                # Treat non-exact matches as ambiguous if you want to distinguish them
                if matchtype and matchtype != "exact":
                    status = "ambiguous"
                else:
                    status = "matched"

            out.append({
                "address_hash": address_hash,
                "geoid": geoid,
                "status": status,
                "result": {
                    "raw": r,              # full Census payload for debugging/audit
                    "benchmark": benchmark,
                    "vintage": vintage,
                },
            })

        # Any rows we sent that did not appear in raw => no_match
        for address_hash, _canon in rows:
            if address_hash in seen_hashes:
                continue
            out.append({
                "address_hash": address_hash,
                "geoid": None,
                "status": "no_match",
                "result": {
                    "reason": "missing_from_census_batch",
                    "benchmark": benchmark,
                    "vintage": vintage,
                },
            })

        return out

    # ---------- public API ----------

    def send_batch(
        self,
        rows: List[Tuple[str, str]],
        *,
        benchmark: str,
        vintage: str,
    ) -> List[Dict[str, Any]]:
        """
        Implementation of the abstract CensusClient API.

        rows: list of (address_hash, address_canonical)
        benchmark/vintage: stored in `result` for traceability and passed
                           into our CensusGeocode object.
        """
        if not rows:
            return []

        # Create our client with the correct benchmark/vintage
        cg = CensusGeocode(benchmark=benchmark, vintage=vintage)

        # Build a temporary batch CSV that the Census API understands
        chunk_file = self._write_chunk_file(rows)

        try:
            raw = CensusHelper.block_to_census(cg, chunk_file, self.n_max_tries,self.timeout,)
        finally:
            # Always clean up the temp file
            try:
                os.remove(chunk_file)
            except OSError:
                pass

        # Convert to our canonical structure
        return self._map_census_result(
            rows,
            raw,
            benchmark=benchmark,
            vintage=vintage,
        )


# ---------- DB helpers ----------

FETCH_FIRST_SQL = text("""
    SELECT a.address_hash, a.address_canonical
    FROM datalake.address a
    LEFT JOIN datalake.census_geocode g
      ON g.address_hash = a.address_hash
     AND g.benchmark = :benchmark
     AND g.vintage   = :vintage
    WHERE g.address_hash IS NULL
    ORDER BY a.address_hash
    LIMIT :lim
""")

FETCH_NEXT_SQL = text("""
    SELECT a.address_hash, a.address_canonical
    FROM datalake.address a
    LEFT JOIN datalake.census_geocode g
      ON g.address_hash = a.address_hash
     AND g.benchmark = :benchmark
     AND g.vintage   = :vintage
    WHERE g.address_hash IS NULL
      AND a.address_hash > :last
    ORDER BY a.address_hash
    LIMIT :lim
""")

def fetch_batch(session: Session, *, benchmark: str, vintage: str, last_key: Optional[str], limit: int) -> List[Tuple[str, str]]:
    if last_key is None:
        rows = session.execute(FETCH_FIRST_SQL, {"benchmark": benchmark, "vintage": vintage, "lim": limit}).all()
    else:
        rows = session.execute(FETCH_NEXT_SQL, {"benchmark": benchmark, "vintage": vintage, "last": last_key, "lim": limit}).all()
    return [(r[0], r[1]) for r in rows]


def upsert_census_results(session: Session, *, benchmark: str, vintage: str, results: List[Dict[str, Any]]) -> int:
    """
    Upsert into datalake.census_geocode using Postgres ON CONFLICT.
    Expects `CensusGeocodeTable` unique key on (address_hash, benchmark, vintage).
    """
    if not results:
        return 0

    rows = []
    for rec in results:
        rows.append({
            "id": new_uuid_str(),                # PK (string UUID)
            "address_hash": rec["address_hash"],
            "benchmark": benchmark,
            "vintage": vintage,
            "geoid": rec.get("geoid"),
            "result": rec.get("result"),
            "status": rec.get("status"),
            # geocoded_at uses server_default=now(); omit to let DB fill it
            "notes": rec.get("notes"),
        })

    stmt = (
        pg_insert(CensusGeocodeTable)
        .values(rows)
        .on_conflict_do_update(
            index_elements=[CensusGeocodeTable.address_hash, CensusGeocodeTable.benchmark, CensusGeocodeTable.vintage],
            set_={
                "geoid": pg_insert(CensusGeocodeTable).excluded.geoid,
                "result": pg_insert(CensusGeocodeTable).excluded.result,
                "status": pg_insert(CensusGeocodeTable).excluded.status,
                # refresh timestamp on update if you prefer:
                # "geocoded_at": func.now(),
                "notes": pg_insert(CensusGeocodeTable).excluded.notes,
            }
        )
    )
    res = session.execute(stmt)
    # For INSERT..ON CONFLICT DO UPDATE, rowcount is the total affected (inserted+updated) rows.
    return res.rowcount or 0


# ---------- Orchestration loop ----------

def run_geocoding(*, benchmark: str, vintage: str, batch_size: int = 5000, client: CensusClient | None = None) -> None:
    db_url = os.environ["DATABASE_URL"]  # fail fast if missing
    engine = create_engine(db_url, future=True)

    if client is None:
        client = MockCensusClient()

    total_sent = 0
    total_upserted = 0
    last_key: Optional[str] = None
    chunk_idx = 0

    with Session(engine, future=True) as session:
        while True:
            # 1) fetch next chunk of addresses that do NOT have a geocode for (benchmark, vintage)
            batch = fetch_batch(session, benchmark=benchmark, vintage=vintage, last_key=last_key, limit=batch_size)
            if not batch:
                break

            # 2) send to Census (client decides HTTP / batch flow)
            results = client.send_batch(batch, benchmark=benchmark, vintage=vintage)

            # 3) upsert results
            affected = upsert_census_results(session, benchmark=benchmark, vintage=vintage, results=results)
            session.commit()

            total_sent += len(batch)
            total_upserted += affected
            chunk_idx += 1
            last_key = batch[-1][0]  # advance keyset cursor

            print(f"[chunk {chunk_idx}] sent={len(batch):,} upserted={affected:,} total_sent={total_sent:,}")

    print(f"[done] total_sent={total_sent:,} total_upserted={total_upserted:,} benchmark={benchmark} vintage={vintage}")


# ---------- CLI ----------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run a geocoding session in 5k chunks")
    ap.add_argument("--benchmark", default="2020")
    ap.add_argument("--vintage", default="2020")
    ap.add_argument("--batch-size", type=int, default=5000)
    ap.add_argument("--mock", action="store_true", help="Use mock client (default if no client is wired)")
    args = ap.parse_args()

    # For now we only wire MockCensusClient; replace with a real client when ready.
    client = MockCensusClient()

    run_geocoding(
        benchmark=args.benchmark,
        vintage=args.vintage,
        batch_size=args.batch_size,
        client=client,
    )
