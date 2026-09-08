<?php

namespace App\Svc;

class Handler
{
    public function run(): void
    {
    }
}

class Dispatch
{
    private Handler $handler;

    public function viaProperty(): void
    {
        $this->handler->run();
    }

    public function viaParameter(Handler $handler): void
    {
        $handler->run();
    }

    public function viaUntyped($handler): void
    {
        $handler->run();
    }
}
