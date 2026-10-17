package p

import "time"

type renewal struct{}

func (dr *renewal) renew() {}

func (dr *renewal) start(d time.Duration) {
	time.AfterFunc(d /* when */, /* what */ dr.renew)
}
