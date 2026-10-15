package native

// #include <stdlib.h>
import "C"

func Alloc() {
	C.malloc(16)
}
