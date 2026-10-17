package p

import "net/http"

func index(w http.ResponseWriter, r *http.Request) {}

func routes() { http.HandleFunc("/", index) }
