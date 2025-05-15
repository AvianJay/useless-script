#include<iostream>
using namespace std;
int main(){
    int classes;
    int max=0;
    cin>>classes;
    for(int i = 0; i<classes; i++){
        int t;
        cin>>t;
        if(t>max){
            max=t;
        }
    }
    cout<<max;
}
