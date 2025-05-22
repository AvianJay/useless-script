#include<iostream>
using namespace std;
int main() {
    int t;
    cin>>t;
    int a[t]={}; // 非必要
    for (int i=0; i<t; i++) {
        int inp;
        cin>>inp;
        a[i] = inp; // 非必要
        cout<<inp<<" ";
    }
}