import pandas as pd
import numpy as np
import sys,time,os
"""
This creates a file which nneds to live in
surgeo/data/prob_race_given_tract_2010.csv
"""

"""
P2_001N	!!Total:	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_001NA	0	int	P2
P2_002N	!!Total:!!Hispanic or Latino	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_002NA	0	int	P2
P2_003N	!!Total:!!Not Hispanic or Latino:	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_003NA	0	int	P2
P2_004N	!!Total:!!Not Hispanic or Latino:!!Population of one race:	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_004NA	0	int	P2
P2_005N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!White alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_005NA	0	int	P2
P2_006N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!Black or African American alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_006NA	0	int	P2
P2_007N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!American Indian and Alaska Native alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_007NA	0	int	P2
P2_008N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!Asian alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_008NA	0	int	P2
P2_009N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!Native Hawaiian and Other Pacific Islander alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_009NA	0	int	P2
P2_010N	!!Total:!!Not Hispanic or Latino:!!Population of one race:!!Some Other Race alone	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_010NA	0	int	P2
P2_011N	!!Total:!!Not Hispanic or Latino:!!Population of two or more races:	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_011NA	0	int	P2
P2_012N	!!Total:!!Not Hispanic or Latino:!!Population of two or more races:!!Population of two races:	HISPANIC OR LATINO, AND NOT HISPANIC OR LATINO BY RACE	not required	P2_012NA	0	int	P2

"""

def state2numpy_race6(filename,state_code):
    # state	state
    # county	county
    # tract	tract
    # white	P2_005N
    # black	P2_006N
    # api	P2_008N+P2_009N
    # native	P2_007N
    # multiple	P2_011N+P2_010N
    # hispanic	P2_002N
    
    df = pd.read_csv(filename,sep=",",compression='infer',low_memory=False)
    K=len(df)

    # some formats are incorrect, so we'll have to fix it
    fmt_d = {'state':"%02d",'county':"%03d",'tract':"%06d"}
    geo_list = list(fmt_d.keys())
    race_list = ["pop_rate_white","pop_rate_black","pop_rate_asian","pop_rate_other","pop_rate_hispanic","pop_rate_native"]
    DType = [(x,'U10') for x in geo_list] + [(x,'f8') for x in race_list]
    
    ar = np.ndarray(K,dtype=DType)
    for i in range(K):
        for geo in geo_list:
            if geo=='state':
                ar[geo][i] = fmt_d[geo] % state_code
            else:
                ar[geo][i] = fmt_d[geo] % int(df.at[i,geo])
    
        P2_002N = df.at[i,'P2_002N']
        P2_005N = df.at[i,'P2_005N']
        P2_006N = df.at[i,'P2_006N']
        P2_007N = df.at[i,'P2_007N']
        P2_008N = df.at[i,'P2_008N']
        P2_009N = df.at[i,'P2_009N']
        P2_010N = df.at[i,'P2_010N']
        P2_011N = df.at[i,'P2_011N']
    
        total = P2_002N+P2_005N+P2_006N+P2_007N+P2_008N+P2_009N+P2_010N+P2_011N
        
        if total>0:
            #pop_rate_white","pop_rate_black","pop_rate_asian","pop_rate_other","pop_rate_hispanic",
            ar['pop_rate_black'][i] = 1.*P2_006N/total
            ar['pop_rate_white'][i] = 1.*P2_005N/total
            ar['pop_rate_hispanic'][i] = 1.*P2_002N/total
            ar['pop_rate_asian'][i] = 1.*(P2_008N+P2_009N)/total
            ar['pop_rate_native'][i] = 1.*P2_007N/total
            ar['pop_rate_other'][i] = 1.*(P2_010N+P2_011N)/total
        
    return ar

def state2numpy_race5(filename,state_code):
    # state	state
    # county	county
    # tract	tract
    # white	P2_005N
    # black	P2_006N
    # api	P2_008N+P2_009N
    # native	P2_007N
    # multiple	P2_011N+P2010N
    # hispanic	P2_002N
    
    df = pd.read_csv(filename,sep=",",compression='infer',low_memory=False)
    K=len(df)

    # some formats are incorrect, so we'll have to fix it
    fmt_d = {'state':"%02d",'county':"%03d",'tract':"%06d"}
    geo_list = list(fmt_d.keys())
    race_list = ["pop_rate_white","pop_rate_black","pop_rate_asian","pop_rate_other","pop_rate_hispanic"]
    DType = [(x,'U10') for x in geo_list] + [(x,'f8') for x in race_list]
    
    ar = np.ndarray(K,dtype=DType)
    for i in range(K):
        for geo in geo_list:
            if geo=='state':
                ar[geo][i] = fmt_d[geo] % state_code
            else:
                ar[geo][i] = fmt_d[geo] % int(df.at[i,geo])
    
        P2_002N = df.at[i,'P2_002N']
        P2_005N = df.at[i,'P2_005N']
        P2_006N = df.at[i,'P2_006N']
        P2_007N = df.at[i,'P2_007N']
        P2_008N = df.at[i,'P2_008N']
        P2_009N = df.at[i,'P2_009N']
        P2_010N = df.at[i,'P2_010N']
        P2_011N = df.at[i,'P2_011N']
    
        total = P2_002N+P2_005N+P2_006N+P2_007N+P2_008N+P2_009N+P2_010N+P2_011N
        
        if total>0:
            #pop_rate_white","pop_rate_black","pop_rate_asian","pop_rate_other","pop_rate_hispanic",
            ar['pop_rate_black'][i] = 1.*P2_006N/total
            ar['pop_rate_white'][i] = 1.*P2_005N/total
            ar['pop_rate_hispanic'][i] = 1.*P2_002N/total
            ar['pop_rate_asian'][i] = 1.*(P2_008N+P2_009N)/total
            ar['pop_rate_other'][i] = 1.*(P2_007N+P2_010N+P2_011N)/total
        
    return ar

##============= MAIN ============================
data_dir = '/home/Data/census'
dest_sub_dir = 'population_distribution_csv_race6'
src_sub_dir = 'pl/2020/P2'
state_code_filename = os.path.join(data_dir,'state_codes.csv')

if not os.path.exists(os.path.join(data_dir,dest_sub_dir)):
    os.makedirs(os.path.join(data_dir,dest_sub_dir))
state_df = pd.read_csv(state_code_filename,sep=",",compression='infer',low_memory=False)
K = len(state_df)
tot_ar = None
for i in range(K):
    letter_code = state_df.at[i,'LetterCode']
    int_code = state_df.at[i,'NumericCode']
    print(i,int_code,letter_code)
    #out_filename = "/home/Data/census/population_distribution_csv/census-2020-tract-%s.csv.gz" % letter_code
    dest_filename = os.path.join(data_dir,dest_sub_dir,"%s.csv.gz" % letter_code)
    src_filename = os.path.join(data_dir,src_sub_dir,"%s.csv.gz" % letter_code)
    ar = state2numpy_race6(src_filename,state_code=int_code)
    tmp_df = pd.DataFrame(ar, columns=ar.dtype.names)
    tmp_df.to_csv(dest_filename,compression="infer",index=False)
    if ar is not None:
        if tot_ar is not None:
            tot_ar = np.hstack((tot_ar,ar))
        else:
            tot_ar = ar
    else:
        print('Could not load data from %s' % src_filename)

df = pd.DataFrame(tot_ar, columns=tot_ar.dtype.names)
dest_filename = os.path.join(data_dir,dest_sub_dir,"%s.csv.gz" % 'ALL')
 
df.to_csv(dest_filename,compression="infer",index=False)