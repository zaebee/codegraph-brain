package money

import "strconv"

// Format renders cents.
func Format(cents int) string {
	return strconv.Itoa(cents)
}
