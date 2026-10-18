package er

import clog "example.com/m/lg"

var log = clog.New("errors")

func report() {
	logFunc := log.Errorf
	logFunc("%d", 1)
}
