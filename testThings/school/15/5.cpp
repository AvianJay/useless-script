#include<iostream>
using namespace std;
int main() {
    int t,bn=0;
    cin>>t;
    int a[t]={};
    for (int i=0; i<t; i++) {
        int inp;
        cin>>inp;
        a[i]=inp;
    }
    for (int i=0; i<t-1; i++) {
        if (a[i]>a[i+1])
            bn++;
    }
    cout<<bn;
}