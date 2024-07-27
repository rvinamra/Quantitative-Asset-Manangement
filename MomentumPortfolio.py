# -*- coding: utf-8 -*-
"""PS3_406306460.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/17uNXWTy0nlh9IyaYp20NhK4WW8g1XDrk
"""

import warnings
warnings.filterwarnings('ignore')

!pip install wrds

!pip install --upgrade numpy scipy

"""# Problem Set 3: Momentum

## Loading Data and other Pre-requisites
"""

import pandas as pd
import numpy as np
import yfinance as yf
import os
import datetime
import time
import matplotlib.pyplot as plt
import wrds
import pandas_datareader
from pandas.tseries.offsets import *

# connecting with the wrds account
conn = wrds.Connection(wrds_username="vrai")

# LOADING CRSP DATA FROM WRDS -- Equity Returns
print('Load Equity Returns Data')
crsp_raw = conn.raw_sql("""
    select a.permno, a.permco, a.date, b.shrcd, b.exchcd,
    a.ret, a.retx, a.shrout, a.prc, a.cfacshr, a.cfacpr
    from crspq.msf as a
    left join crsp.msenames as b
    on a.permno=b.permno
    and b.namedt<=a.date
    and a.date<=b.nameendt
    where a.date between '01/01/1900' and '12/31/2023'
""")

crsp_raw = crsp_raw.sort_values(['permno', 'date']).reset_index(drop=True).copy()
crsp_raw['permno'] = crsp_raw['permno'].astype(int)
crsp_raw['permco'] = crsp_raw['permco'].astype(int)
crsp_raw['date'] = pd.to_datetime(crsp_raw['date'], format='%Y-%m-%d', errors='ignore') + MonthEnd(0)

# LOADING CRSP DATA FROM WRDS -- Delisting Returns
print('Load Delisting Returns Data')
dlret_raw = conn.raw_sql("""
    select permno, dlret, dlstdt, dlstcd
    from crspq.msedelist
""")
dlret_raw = dlret_raw.sort_values(['permno', 'dlstdt']).reset_index(drop=True).copy()
dlret_raw.permno = dlret_raw.permno.astype(int)
dlret_raw['dlstdt'] = pd.to_datetime(dlret_raw['dlstdt'])
dlret_raw['date'] = dlret_raw['dlstdt'] + MonthEnd(0)

# merging the delisted returns with the original returns in crsp data
crsp_dlret_merged = pd.merge(crsp_raw, dlret_raw[['permno', 'date', 'dlret']],
                             on=['permno', 'date'],
                             how='left')
crsp_dlret_merged['dlret'].fillna(0, inplace=True)

crsp_dlret_merged.head()

"""## Problem 1: Ranked Monthly Returns using Daniel and Moskowitz (2016) methodology"""

def PS3_Q1(data):

    data['date'] = pd.to_datetime(data['date'])

    # filtering correct EXCHCD and SHRCD
    data = data[(data['exchcd'].isin([1, 2, 3])) & (data['shrcd'].isin([10, 11]))]

    # cleaning 'RET' and 'DLRET'
    data['ret'] = pd.to_numeric(data['ret'], errors='coerce').fillna(0)
    data['dlret'] = pd.to_numeric(data['dlret'], errors='coerce').fillna(0)

    # calculating total returns combining RET and DLRET using logarithmic transformation
    data['Ret'] = np.log(1 + data['ret']) + np.log(1 + data['dlret'])

    # calculating market capitalization in millions and ensure market cap at t-1 is not missing
    data['Mkt_Cap'] = data['shrout'] * np.abs(data['prc']) / 1000
    data['lag_Mkt_Cap'] = data.groupby('permno')['Mkt_Cap'].shift(1)
    data = data.dropna(subset=['lag_Mkt_Cap'])  # Ensure me(t-1) not missing

    # dropping rows missing the price or shares outstanding or RET
    data = data.dropna(subset=['prc', 'shrout', 'Ret'])

    # calculating cumulative log returns for ranking, from t-12 through t-2
    data['Ranking_Ret'] = data.groupby('permno')['Ret'].transform(
        lambda x: x.shift(2).rolling(window=11, min_periods=11).sum()
    )

    # ensuring that ret(t-2) and price at t-13 are not missing
    data['ret_t2'] = data.groupby('permno')['Ret'].shift(2)
    data['price_t13'] = data.groupby('permno')['prc'].shift(13)
    data = data.dropna(subset=['ret_t2', 'price_t13'])

    # extracting year and month from the date
    data['Year'] = data['date'].dt.year
    data['Month'] = data['date'].dt.month

    # filtering data for the years 1927 to 2023
    data = data[(data['Year'] >= 1927) & (data['Year'] <= 2023)]

    data = data[['Year', 'Month', 'permno', 'exchcd', 'lag_Mkt_Cap', 'ret', 'Ranking_Ret']]
    data = data.sort_values(by=['Year', 'Month', 'permno'])

    return data

CRSP_Stocks_Momentum = PS3_Q1(crsp_dlret_merged)

CRSP_Stocks_Momentum.reset_index(drop=True, inplace=True)
CRSP_Stocks_Momentum.head()

"""## Problem 2: Monthly Momentum Portfolio Decile using Daniel and Moskowitz (2016) and Ken French methodology"""

def PS3_Q2(data):

    data.columns = data.columns.str.lower()

    # creating a new date column from Year and Month
    data['date'] = data['year'] * 100 + data['month']
    data.sort_values(['date', 'exchcd'], inplace=True)

    # calculating decile ranks for 'ranking_ret' for each date
    data['dm_decile'] = data.groupby('date')['ranking_ret'].transform(
        lambda x: pd.qcut(x, 10, labels=False, duplicates='drop') + 1)

    # preparing a DataFrame to calculate quantiles
    D = pd.DataFrame({'date': data['date'].unique()})
    quantiles = [i / 10 for i in range(1, 11)]

    # calculating quantiles for EXCHCD == 1
    quantile_values = {}
    for q in quantiles:
        quantile_values[f'quantile_{int(q*10)}'] = []

    for date in D['date']:
        subset = data[(data['date'] == date) & (data['exchcd'] == 1)]
        if not subset.empty:
            q_values = subset['ranking_ret'].quantile(quantiles)
            for idx, q in enumerate(quantiles):
                quantile_values[f'quantile_{int(q*10)}'].append(q_values[q])
        else:
            for key in quantile_values:
                quantile_values[key].append(np.nan)

    for key, value in quantile_values.items():
        D[key] = value

    # merging the quantile data back to the original data frame
    data = pd.merge(data, D, on='date', how='left')

    # Calculate KRF_decile based on quantiles
    def apply_quantiles(row):
        for idx in range(1, 11):
            if row['ranking_ret'] <= row[f'quantile_{idx}']:
                return idx
        return 10

    data['krf_decile'] = data.apply(apply_quantiles, axis=1)

    return data[['year', 'month', 'permno', 'lag_mkt_cap', 'ret', 'dm_decile', 'krf_decile']]

CRSP_Stocks_Momentum_decile = PS3_Q2(CRSP_Stocks_Momentum)

CRSP_Stocks_Momentum_decile.head()

"""## Problem 3: Monthly Momentum Portfolio Decile Returns using Daniel and Moskowitz (2016) and Ken French methodology"""

# loading data from Ken French website
FF3 = pandas_datareader.famafrench.FamaFrenchReader('F-F_Research_Data_Factors', start='1900', end=str(datetime.datetime.now().year+1))
FF3 = FF3.read()[0] / 100  # Monthly data
FF3.columns = ['MktRF', 'SMB', 'HML', 'RF']
FF3['Mkt'] = FF3['MktRF'] + FF3['RF']
FF3 = FF3.reset_index().rename(columns={"Date":"date"}).copy()
FF3['date'] = pd.DataFrame(FF3['date'].values.astype('datetime64[M]')) + MonthEnd(0)
FF3.rename(columns={'MktRF': 'Market_minus_Rf', 'RF': 'Rf'}, inplace=True)
FF3.head()

def PS3_Q3(CRSP_Stocks_Momentum_decile, FF_mkt):

    FF_mkt['date'] = pd.to_datetime(FF_mkt['date'])
    FF_mkt['year'] = FF_mkt['date'].dt.year
    FF_mkt['month'] = FF_mkt['date'].dt.month

    # merging market data with stocks momentum decile data
    merged_data = pd.merge(CRSP_Stocks_Momentum_decile, FF_mkt, on=['year', 'month'], how='inner')

    # calculating weighted returns and merge DM and KRF results
    def calculate_weighted_returns(group):
        total_lag_mkt_cap = group['lag_mkt_cap'].sum()
        weighted_avg_return = (group['ret'] * group['lag_mkt_cap']).sum() / total_lag_mkt_cap if total_lag_mkt_cap != 0 else 0
        return pd.Series({
            'Total_lag_MCap': total_lag_mkt_cap,
            'Weighted_Avg_Return': weighted_avg_return
        })

    # applying the function to both DM and KRF decile data
    dm_results = merged_data.groupby(['year', 'month', 'dm_decile']).apply(calculate_weighted_returns).reset_index()
    dm_results.rename(columns={'dm_decile': 'Decile', 'Weighted_Avg_Return': 'DM_Avg_Return'}, inplace=True)
    krf_results = merged_data.groupby(['year', 'month', 'krf_decile']).apply(calculate_weighted_returns).reset_index()
    krf_results.rename(columns={'krf_decile': 'Decile', 'Weighted_Avg_Return': 'KRF_Avg_Return'}, inplace=True)

    # merging DM and KRF results on common identifiers
    final_output = pd.merge(dm_results, krf_results, on=['year', 'month', 'Decile'], how='outer')

    # including the Risk-Free Rate (Rf) for each month in the final output
    final_output = pd.merge(final_output, FF_mkt[['year', 'month', 'Rf']].drop_duplicates(), on=['year', 'month'], how='left')

    return final_output[['year', 'month', 'Decile', 'DM_Avg_Return', 'KRF_Avg_Return', 'Rf']]

CRSP_Stocks_Momentum_returns = PS3_Q3(CRSP_Stocks_Momentum_decile, FF3)

CRSP_Stocks_Momentum_returns.tail()

"""## Problem 4: Recreation of Table 1 from Daniel and Moskowitz (2016)"""

from google.colab import drive
drive.mount('/content/drive')

# laoding the file
dm_momentum = pd.read_csv('/content/drive/My Drive/m_m_pt_tot.txt', header=None, delim_whitespace=True)

column_names = ['Date', 'Decile', 'DM_Avg_Return', 'column_4', 'column_5']
dm_momentum.columns = column_names

# converting 'Date' column to datetime
dm_momentum['Date'] = pd.to_datetime(dm_momentum['Date'], format='%Y%m%d')  # Adjust the format according to your data

# extracting Year and Month from the 'Date' column
dm_momentum['year'] = dm_momentum['Date'].dt.year
dm_momentum['month'] = dm_momentum['Date'].dt.month
dm_momentum = dm_momentum[['year', 'month', 'Decile', 'DM_Avg_Return']]

dm_momentum.head()

from scipy.stats import skew

def PS3_Q4(Input, DM_returns):
    # calculating risk-adjusted returns, standard deviation, and Sharpe Ratio for each decile
    grouped = Input.groupby('Decile').apply(lambda x: pd.Series({
        'r_rf': (x['DM_Avg_Return'] - x['Rf']).mean() * 12,
        'sd': (x['DM_Avg_Return'] - x['Rf']).std() * np.sqrt(12),
        'sk': skew(x['DM_Avg_Return'])
    }))
    grouped['SR'] = grouped['r_rf'] / grouped['sd']

    # calculating the difference in returns between decile 10 and 1 (Winner minus Loser)
    wml = Input[Input['Decile'] == 10]['DM_Avg_Return'].values - Input[Input['Decile'] == 1]['DM_Avg_Return'].values

    # computing WML statistics
    Output = grouped.T
    Output.columns = [f"Decile {i+1}" for i in range(10)]
    Output['WML'] = Output['Decile 10'] - Output['Decile 1']
    Output.loc['sd', 'WML'] = np.std(wml) * np.sqrt(12)
    Output.loc['SR', 'WML'] = Output.loc['r_rf', 'WML'] / Output.loc['sd', 'WML']

    # calculating WML Skewness after adding risk-free rate
    wml1 = np.log(1 + wml + Input[Input['Decile'] == 10]['Rf'].values)
    Output.loc['sk', 'WML'] = skew(wml1)

    # calculating WML for the Input data
    wml_input = Input.groupby(['year', 'month']).apply(
        lambda x: x[x['Decile'] == 10]['DM_Avg_Return'].mean() - x[x['Decile'] == 1]['DM_Avg_Return'].mean()
    )

    # calculating WML for the DM_returns data
    wml_dm = DM_returns.groupby(['year', 'month']).apply(
        lambda x: x[x['Decile'] == 10]['DM_Avg_Return'].mean() - x[x['Decile'] == 1]['DM_Avg_Return'].mean()
    )

    # creating a DataFrame for correlation calculation
    wml_comparison = pd.DataFrame({
        'WML_Input': wml_input,
        'WML_DM': wml_dm
    }).dropna()

    # calculating the correlation with DM_returns
    merged = pd.merge(Input, DM_returns, on=['year', 'month', 'Decile'])

    correlations = merged.groupby('Decile').apply(lambda x: x['DM_Avg_Return_x'].corr(x['DM_Avg_Return_y']))
    Output.loc['Correlation with DM', :] = correlations.tolist() + [np.nan]

    wml_correlation = wml_input.corr(wml_dm)
    Output.loc['Correlation with DM', 'WML'] = wml_correlation

    return Output

DM_statistics = PS3_Q4(CRSP_Stocks_Momentum_returns, dm_momentum)

DM_statistics

"""## Problem 5: Recreation of Table 1 from Daniel and Moskowitz (2016) using Ken French breakpoints"""

# loading the file
kf_momentum = pd.read_csv('/content/drive/My Drive/m_m_pt_nyse_tot.txt', header=None, delim_whitespace=True)

# renaming the columns
column_names = ['Date', 'Decile', 'KRF_Avg_Return', 'column_4', 'column_5']
kf_momentum.columns = column_names

kf_momentum['Date'] = pd.to_datetime(kf_momentum['Date'], format='%Y%m%d')  # Adjust the format according to your data

# extracting Year and Month from the 'Date' column
kf_momentum['year'] = kf_momentum['Date'].dt.year
kf_momentum['month'] = kf_momentum['Date'].dt.month
kf_momentum = kf_momentum[['year', 'month', 'Decile', 'KRF_Avg_Return']]

kf_momentum['Decile'] = pd.to_numeric(kf_momentum['Decile'], errors='coerce')  # Convert to numeric, make non-convertible as NaN
kf_momentum = kf_momentum.dropna(subset=['Decile'])

kf_momentum.head()

def PS3_Q5(Input, KRF_returns):
    # calculating risk-adjusted returns, standard deviation, and Sharpe Ratio for each decile
    grouped = Input.groupby('Decile').apply(lambda x: pd.Series({
        'r_rf': (x['KRF_Avg_Return'] - x['Rf']).mean() * 12,
        'sd': (x['KRF_Avg_Return'] - x['Rf']).std() * np.sqrt(12),
        'sk': skew(x['KRF_Avg_Return'])
    }))
    grouped['SR'] = grouped['r_rf'] / grouped['sd']

    # calculating the difference in returns between decile 10 and 1 (Winner minus Loser)
    wml = Input[Input['Decile'] == 10]['KRF_Avg_Return'].values - Input[Input['Decile'] == 1]['KRF_Avg_Return'].values

    # computing WML statistics
    Output = grouped.T
    Output.columns = [f"Decile {i+1}" for i in range(10)]
    Output['WML'] = Output['Decile 10'] - Output['Decile 1']
    Output.loc['sd', 'WML'] = np.std(wml) * np.sqrt(12)
    Output.loc['SR', 'WML'] = Output.loc['r_rf', 'WML'] / Output.loc['sd', 'WML']

    # calculating WML Skewness after adding risk-free rate
    wml1 = np.log(1 + wml + Input[Input['Decile'] == 10]['Rf'].values)
    Output.loc['sk', 'WML'] = skew(wml1)

    # calculating WML for the Input data
    wml_input = Input.groupby(['year', 'month']).apply(
        lambda x: x[x['Decile'] == 10]['KRF_Avg_Return'].mean() - x[x['Decile'] == 1]['KRF_Avg_Return'].mean()
    )

    # calculating WML for the DM_returns data
    wml_dm = KRF_returns.groupby(['year', 'month']).apply(
        lambda x: x[x['Decile'] == 10]['KRF_Avg_Return'].mean() - x[x['Decile'] == 1]['KRF_Avg_Return'].mean()
    )

    # creating a DataFrame for correlation calculation
    wml_comparison = pd.DataFrame({
        'WML_Input': wml_input,
        'WML_DM': wml_dm
    }).dropna()

    # calculating the correlation with DM_returns
    merged = pd.merge(Input, KRF_returns, on=['year', 'month', 'Decile'])

    correlations = merged.groupby('Decile').apply(lambda x: x['KRF_Avg_Return_x'].corr(x['KRF_Avg_Return_y']))
    Output.loc['Correlation with KRF', :] = correlations.tolist() + [np.nan]  # NaN for WML as it's not applicable directly

    wml_correlation = wml_input.corr(wml_dm)
    Output.loc['Correlation with KRF', 'WML'] = wml_correlation

    return Output

KRF_statistics = PS3_Q5(CRSP_Stocks_Momentum_returns, kf_momentum)

KRF_statistics

"""## Problem 6: Performance of Momentum Strategy"""

# creating a function to compute WML for each group
def compute_wml(group):
    decile_10_return = group[group['Decile'] == 10]['DM_Avg_Return'].mean()
    decile_1_return = group[group['Decile'] == 1]['DM_Avg_Return'].mean()
    return decile_10_return - decile_1_return

# grouping by Year and Month and apply the WML computation
wml_data = CRSP_Stocks_Momentum_returns.groupby(['year', 'month']).apply(compute_wml).reset_index(name='WML')

wml_data['date'] = pd.to_datetime(wml_data['year'].astype(str) + '-' + wml_data['month'].astype(str))

wml_dataframe = wml_data[['date', 'WML']]

wml_dataframe.head()

# plotting returns for the last 10 years and 5 years
last_10_years = wml_dataframe[wml_dataframe['date'] >= pd.Timestamp.now() - pd.DateOffset(years=10)]
last_5_years = wml_dataframe[wml_dataframe['date'] >= pd.Timestamp.now() - pd.DateOffset(years=5)]

plt.figure(figsize=(20, 6))
plt.plot(last_10_years['date'], last_10_years['WML'], label='Last 10 Years')
plt.plot(last_5_years['date'], last_5_years['WML'], label='Last 5 Years', color='red')
plt.title('WML Time Series')
plt.xlabel('Date')
plt.ylabel('WML Returns')
plt.legend()
plt.grid(True)
plt.show()

FF3['Market_Cumulative_Returns'] = (1 + FF3['Market_minus_Rf']).cumprod()
wml_dataframe['Cumulative_Returns'] = (1 + wml_dataframe['WML']).cumprod()

wml_dataframe['Date'] = pd.to_datetime(wml_dataframe['date']).dt.to_period('M').dt.to_timestamp('M')
FF3['Date'] = pd.to_datetime(FF3['date']).dt.to_period('M').dt.to_timestamp('M')

# merging the dataframes on the 'Date' field
cumulative_returns = pd.merge(wml_dataframe[['Date', 'Cumulative_Returns']],
                              FF3[['Date', 'Market_Cumulative_Returns']],
                              on='Date', how='left')

cumulative_returns.head()

# filtering data for the last 10 and 5 years as previously defined
current_year = pd.Timestamp.now().year
last_10_years_data = cumulative_returns[(cumulative_returns['Date'].dt.year > current_year - 10)]
last_5_years_data = cumulative_returns[(cumulative_returns['Date'].dt.year > current_year - 5)]

# normalizing to start from 1
last_10_years_data['Normalized_WML'] = last_10_years_data['Cumulative_Returns'] / last_10_years_data.iloc[0]['Cumulative_Returns']
last_10_years_data['Normalized_Market'] = last_10_years_data['Market_Cumulative_Returns'] / last_10_years_data.iloc[0]['Market_Cumulative_Returns']
last_5_years_data['Normalized_WML'] = last_5_years_data['Cumulative_Returns'] / last_5_years_data.iloc[0]['Cumulative_Returns']
last_5_years_data['Normalized_Market'] = last_5_years_data['Market_Cumulative_Returns'] / last_5_years_data.iloc[0]['Market_Cumulative_Returns']

# plotting Last 10 years Cumulative Returns
plt.figure(figsize=(20, 6))
plt.plot(last_10_years_data['Date'], last_10_years_data['Normalized_WML'], label='WML Last 10 Years')
plt.plot(last_10_years_data['Date'], last_10_years_data['Normalized_Market'], label='Market Last 10 Years', linestyle='--')
plt.title('Normalized Cumulative Returns of WML and Market Minus RF')
plt.xlabel('Date')
plt.ylabel('Cumulative Returns (Normalized to start from 1)')
plt.legend()
plt.grid(True)
plt.show()

# plotting Last 5 years Cumulative Returns

plt.figure(figsize=(20, 6))
plt.plot(last_5_years_data['Date'], last_5_years_data['Normalized_WML'], label='WML Last 5 Years', color='red')
plt.plot(last_5_years_data['Date'], last_5_years_data['Normalized_Market'], label='Market Last 5 Years', color='green', linestyle='--')
plt.title('Normalized Cumulative Returns of WML and Market Minus RF')
plt.xlabel('Date')
plt.ylabel('Cumulative Returns (Normalized to start from 1)')
plt.legend()
plt.grid(True)
plt.show()

# calculating Average Returns and Sharpe Ratio
def calculate_metrics(data, column):
    avg_return = data[column].mean() * 12
    std_dev = data[column].std() * np.sqrt(12)
    sharpe_ratio = avg_return / std_dev # Annualizing Sharpe Ratio
    return avg_return, std_dev, sharpe_ratio

current_year = pd.Timestamp.now().year
date_filter_10y_wml = wml_dataframe['Date'].dt.year >= current_year - 10
date_filter_5y_wml = wml_dataframe['Date'].dt.year >= current_year - 5

date_filter_10y_mkt = FF3['Date'].dt.year >= current_year - 10
date_filter_5y_mkt = FF3['Date'].dt.year >= current_year - 5

last_10_years_data_wml = wml_dataframe[date_filter_10y_wml]
last_5_years_data_wml = wml_dataframe[date_filter_5y_wml]

last_10_years_data_mkt = FF3[date_filter_10y_mkt]
last_5_years_data_mkt = FF3[date_filter_5y_mkt]

# calculating for WML
wml_10y_metrics = calculate_metrics(last_10_years_data_wml, 'WML')
wml_5y_metrics = calculate_metrics(last_5_years_data_wml, 'WML')

# calculating for Market
market_10y_metrics = calculate_metrics(last_10_years_data_mkt, 'Market_minus_Rf')
market_5y_metrics = calculate_metrics(last_5_years_data_mkt, 'Market_minus_Rf')

# creating DataFrame to store metrics with formatting for percentages and rounding
metrics_data = pd.DataFrame({
    'Period': ['Last 10 Years', 'Last 5 Years'],
    'WML Avg Return (%)': [f"{x * 100:.2f}%" for x in [wml_10y_metrics[0], wml_5y_metrics[0]]],
    'WML Volatility (%)': [f"{x * 100:.2f}%" for x in [wml_10y_metrics[1], wml_5y_metrics[1]]],
    'WML Sharpe Ratio': [f"{x:.4f}" for x in [wml_10y_metrics[2], wml_5y_metrics[2]]],
    'Market Avg Return (%)': [f"{x * 100:.2f}%" for x in [market_10y_metrics[0], market_5y_metrics[0]]],
    'Market Volatility (%)': [f"{x * 100:.2f}%" for x in [market_10y_metrics[1], market_5y_metrics[1]]],
    'Market Sharpe Ratio': [f"{x:.4f}" for x in [market_10y_metrics[2], market_5y_metrics[2]]]
})

# displaying the DataFrame
metrics_data