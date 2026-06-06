# 🏗️ Blueprint: Code Graph Resolution Engine (CGRE)

## 1. 🧠 Memory Model (Data Structures)

Чтобы машина знала, "кто есть кто", ей нужны три типа памяти.

### A. Global Symbol Table (GST) — "Вселенная"
Это глобальный реестр всех объявленных сущностей.
*   **Key:** `FQN` (Fully Qualified Name), например: `project.core.auth.UserService`
*   **Value:** `NodeID` (ссылка на узел в графе) + `Metadata` (тип, файл, координаты).
*   *Цель:* Дать быстрый ответ на вопрос: "Существует ли в проекте вообще такой объект?"

### B. Local Namespace Map (LNM) — "Переводчик"
Для каждого файла создается свой словарь "локальных имен" $\to$ "глобальных FQN".
*   **Key:** `local_name` (например, `auth_user`)
*   **Value:** `FQN` (например, `project.core.auth.User`)
*   *Цель:* Решить проблему алиасов (`import x as y`) и импортов.

### C. Scope Stack — "Навигатор"
Это динамическая память, которая живет только во время обхода конкретного файла. Она хранит текущий путь "глубины".
*   **Stack Item:** `{ type: (CLASS|FUNC|MODULE), fqn: string, local_vars: List[string] }`
*   *Пример стека внутри метода:* 
    1. `[Module: project.app]`
    2. `[Class: AppService]`
    3. `[Method: run_task]` $\leftarrow$ *Current*

---

## 2. 🔄 The Three-Pass Pipeline (Workflow)

Машина работает в три этапа. Мы не можем соединить связи, пока не проиндексируем всё.

### Pass 1: Discovery (The Indexer)
**Задача:** Наполнить `GST`.
1. Идем по всем файлам.
2. Для каждого `class_definition` и `function_definition` вычисляем их FQN (на основе пути файла и иерархии).
3. Записываем в `GST`.
4. *Результат:* Мы знаем, что в мире существуют `A`, `B` и `C`.

### Pass 2: Translation (The Mapper)
**Задача:** Наполнить `LNM` для каждого файла.
1. Идем по файлам.
2. Ищем узлы `import_statement` и `import_from`.
3. Для каждого импорта в `LNM` текущего файла записываем: `local_name` $\to$ `target_FQN`.
4. *Результат:* Мы знаем, что в файле `main.py` имя `auth` на самом деле означает `project.core.auth`.

### Pass 3: Linking (The Resolver) — "Сердце"
**Задача:** Найти `Call` и создать `Edge`.
1. Идем по файлам.
2. Для каждого файла создаем пустой `Scope Stack`.
3. Начинаем обход AST. При входе в `class` или `def` — `push` в стек. При выходе — `pop`.
4. При встрече узла `Call(name)` запускаем **Resolution Logic**.

---

## 3. ⚡ Resolution Logic (The Decision Tree)

Когда мы нашли вызов `target_name`, машина должна принять решение.

**Вход:** `target_name` (напр. `get_user`), `current_file_context`, `current_scope_stack`.

### Шаг 1: Проверка на "Квалифицированный вызов" (Qualified Call)
*Если в коде написано `math.sqrt()` или `self.do_work()`:*
1. Разделяем на `prefix` (`math` или `self`) и `name` (`sqrt` или `do_work`).
2. **Если `prefix == 'self'`:**
   - Берем из стека `current_scope`. Если мы в методе класса `X`, то `Target_FQN = X.do_work`.
3. **Если `prefix` — это что-то другое (напр. `math`):**
   - Ищем `prefix` в `LNM` текущего файла.
   - Если нашли: `prefix_fqn = LNM[prefix]`.
   - `Target_FQN = prefix_fqn + "." + name`.
4. **Если не нашли в `LNM`:**
   - Помечаем как `Unresolved_Qualified_Call`.

### Шаг 2: Проверка на "Локальный/Глобальный вызов" (Unqualified Call)
*Если в коде написано просто `get_user()`:*
1. **Проверка LNM:** Есть ли `get_user` в `LNM` текущего файла? (Это импорт).
   - Да $\to$ `Target_FQN = LNM['get_user']`.
2. **Проверка Scope:** Есть ли `get_user` в `local_vars` текущего уровня стека? (Это переменная или вложенная функция).
   - Да $\to$ `Target_FQN = current_scope.fqn + ".get_user"`.
3. **Проверка Class Members:** Если мы внутри класса `X`, есть ли у `X` метод `get_user`?
   - Да $\to$ `Target_FQN = X.get_user`.
4. **Проверка Global:** Есть ли `get_user` в `GST` как глобальная функция?
   - Да $\to$ `Target_FQN = GST['get_user']`.

### Шаг 3: Финализация
*   Если `Target_FQN` найден в `GST` $\to$ **Создаем Edge (CALLS) с confidence=1.0**.
*   Если не найден $\to$ **Создаем Edge (UNKNOWN_CALL) с confidence=0.1**.

---

## 4. 🧪 The "Self-Parsing" Test (The Ultimate Validation)

Чтобы проверить машину, мы подаем ей на вход её собственный код.

**Ожидаемый результат:**
1. `GST` должна содержать FQN всех модулей резолвера.
2. `LNM` должна правильно разрешить `import tree_sitter`.
3. `Linking Pass` должен найти связь: `ResolutionEngine.resolve_call` $\to$ `CALLS` $\to$ `AST_Node.name`.

**Если граф самопостроения совпадает со структурой папок и файлов — машина работает идеально.**

