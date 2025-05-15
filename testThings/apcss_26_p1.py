inp = input().split()
n = int(inp[0])
k = int(inp[1])
np = {}
ti = input().split()
for i in range(0,n*2,2):
  np[int(ti[i])] = int(ti[i+1])
s=0
for i in range(k):
  tt = input().split()
  for j in range(0,k*2,2):
    #print(int(tt[j]), np.keys())
    if int(tt[j]) in np.keys():
      s+=np[int(tt[j])]*int(tt[j+1])
      #print(s)
print(s)