<?php

require_once __DIR__ . '/inc/helpers.php';

$driver = getenv('APP_ENV');
include $driver . '.php';
