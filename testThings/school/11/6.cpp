#include <iostream>
using namespace std;
int main(){
    int n,m,o;
    long long t=0;
    cin>>n>>m>>o;
    for (int i=n; i<=m; i+=o)
        t+=i;
    cout<<t;
}
