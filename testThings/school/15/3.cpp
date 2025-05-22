#include<iostream>
using namespace std;
int main() {
    int t,f=0;
    cin>>t;
    for (int i=0; i<t; i++) {
        int inp;
        cin>>inp;
        f+=inp;
    }
    cout<<f;
}