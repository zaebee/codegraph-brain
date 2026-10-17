package p

import "strings"

func upper(r rune) rune { return r - 32 }

func shout(s string) string { return strings.Map(upper, s) }
