package main

import (
	"fmt"

	"example.com/money"
	"example.com/shop/internal/server"
	"example.com/shop/internal/store"
	"github.com/google/uuid"
)

func main() {
	db := &store.Store{}
	s := server.New(db)
	s.Start()
	fmt.Println(money.Format(42), uuid.NewString())
}
