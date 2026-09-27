"""Commands — the I/O side. They take Services, do work, dispatch Events back.

A Command is the only place that touches the transport, the database or the
network. Reducers stay pure; views stay dumb. Nothing here returns state — it
dispatches Events and the store decides what the new state is.
"""
