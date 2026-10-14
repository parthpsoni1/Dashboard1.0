import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_excel(r"C:\Users\perth.soni\PycharmProjects\DashBoard\Raws\Transactions from Jan to Sep 25.xlsx", sheet_name="Sheet 2")
print(df.head())
df['Date'] = pd.to_datetime(df['Date'])
# Example of checking correlation per category
results = df.groupby('Items')[['Unit Price','Quantity']].corr().iloc[0::2,-1]
results.to_excel('Corr.xlsx')


